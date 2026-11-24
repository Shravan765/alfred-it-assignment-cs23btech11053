'''Validated, immutable configuration loading shared by the server and agent.'''

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.action_kinds import ACTION_KINDS


class ConfigError(ValueError):
	'''Raised with all configuration problems found during one load.'''


@dataclass(frozen=True)
class PathsSettings:
	'''Filesystem locations used by the application.'''
	data_dir: str
	database_file: str
	credentials_file: str
	bootstrap_dir: str


@dataclass(frozen=True)
class LoggingSettings:
	'''Logging verbosity and actor inclusion.'''
	level: str
	include_actor: bool


@dataclass(frozen=True)
class DatabaseSettings:
	'''SQLite connection behaviour.'''
	busy_timeout_ms: int
	journal_mode: str


@dataclass(frozen=True)
class AuthSettings:
	'''User session and password hashing parameters.'''
	session_ttl_s: int
	token_bytes: int
	scrypt_n: int
	scrypt_r: int
	scrypt_p: int


@dataclass(frozen=True)
class ThrottleSettings:
	'''Login and registration throttling parameters.'''
	login_max_failures: int
	login_window_s: int
	register_max_failures: int
	register_window_s: int


@dataclass(frozen=True)
class LimitsSettings:
	'''HTTP request limits.'''
	max_body_bytes: int


@dataclass(frozen=True)
class CodesSettings:
	'''One-time registration code policy.'''
	ttl_s: int
	length: int
	group_size: int
	max_active: int


@dataclass(frozen=True)
class AgentsSettings:
	'''Agent naming and heartbeat policy.'''
	heartbeat_interval_s: int
	online_threshold_s: int
	name_max_length: int
	info_max_bytes: int


@dataclass(frozen=True)
class ActionsSettings:
	'''Action queue policy.'''
	enabled_kinds: tuple[str, ...]
	max_queued_per_agent: int
	claim_batch_size: int
	dispatch_expiry_s: int
	queued_expiry_s: int
	output_max_bytes: int
	history_default_limit: int
	history_max_limit: int


@dataclass(frozen=True)
class AgentSettings:
	'''Agent connection, retry and simulation settings.'''
	server_url: str
	ca_bundle: str
	state_dir: str
	bootstrap_dir: str
	connect_timeout_s: int
	read_timeout_s: int
	backoff_initial_s: int
	backoff_max_s: int
	backoff_jitter: float
	action_timeout_s: int
	sim_root: str
	demo_spool_jobs: int
	disk_usage_path: str


@dataclass(frozen=True)
class SeedUser:
	'''A configured sample user.'''
	username: str
	role: str


@dataclass(frozen=True)
class SeedSettings:
	'''Initial sample data.'''
	password_length: int
	bootstrap_agent_names: tuple[str, ...]
	users: tuple[SeedUser, ...]


@dataclass(frozen=True)
class Settings:
	'''Complete immutable application configuration.'''
	paths: PathsSettings
	logging: LoggingSettings
	database: DatabaseSettings
	auth: AuthSettings
	throttle: ThrottleSettings
	limits: LimitsSettings
	codes: CodesSettings
	agents: AgentsSettings
	actions: ActionsSettings
	agent: AgentSettings
	seed: SeedSettings


# Each entry defines accepted scalar types and inclusive bounds. Booleans are
# checked separately because bool is a subclass of int in Python.
SCHEMA = {
	'paths': {'data_dir': ('str', None, None), 'database_file': ('filename', None, None), 'credentials_file': ('filename', None, None), 'bootstrap_dir': ('str', None, None)},
	'logging': {'level': ('choice', ('DEBUG', 'INFO', 'WARNING', 'ERROR'), None), 'include_actor': ('bool', None, None)},
	'database': {'busy_timeout_ms': ('int', 100, 60000), 'journal_mode': ('choice', ('WAL', 'DELETE'), None)},
	'auth': {'session_ttl_s': ('int', 60, 604800), 'token_bytes': ('int', 16, 64), 'scrypt_n': ('int', 1024, 1048576), 'scrypt_r': ('int', 1, 32), 'scrypt_p': ('int', 1, 4)},
	'throttle': {'login_max_failures': ('int', 1, 1000), 'login_window_s': ('int', 10, 86400), 'register_max_failures': ('int', 1, 1000), 'register_window_s': ('int', 10, 86400)},
	'limits': {'max_body_bytes': ('int', 1024, 1048576)},
	'codes': {'ttl_s': ('int', 60, 2592000), 'length': ('int', 12, 32), 'group_size': ('int', 2, 8), 'max_active': ('int', 1, 10000)},
	'agents': {'heartbeat_interval_s': ('int', 1, 3600), 'online_threshold_s': ('int', 2, 86400), 'name_max_length': ('int', 8, 100), 'info_max_bytes': ('int', 128, 65536)},
	'actions': {'enabled_kinds': ('strings', None, None), 'max_queued_per_agent': ('int', 1, 100), 'claim_batch_size': ('int', 1, 100), 'dispatch_expiry_s': ('int', 1, 604800), 'queued_expiry_s': ('int', 60, 604800), 'output_max_bytes': ('int', 256, 65536), 'history_default_limit': ('int', 1, 200), 'history_max_limit': ('int', 1, 1000)},
	'agent': {'server_url': ('str', None, None), 'ca_bundle': ('str', None, None), 'state_dir': ('str', None, None), 'bootstrap_dir': ('str', None, None), 'connect_timeout_s': ('int', 1, 120), 'read_timeout_s': ('int', 1, 120), 'backoff_initial_s': ('int', 1, 60), 'backoff_max_s': ('int', 1, 3600), 'backoff_jitter': ('float', 0.0, 1.0), 'action_timeout_s': ('int', 1, 600), 'sim_root': ('str', None, None), 'demo_spool_jobs': ('int', 0, 1000), 'disk_usage_path': ('str', None, None)},
	'seed': {'password_length': ('int', 12, 64), 'bootstrap_agent_names': ('strings', None, None), 'users': ('users', None, None)},
}


def readToml(path: str | Path, problems: list[str]) -> dict[str, Any]:
	'''Read a TOML file and report parse or filesystem errors.'''
	try:
		with Path(path).open('rb') as handle:
			return tomllib.load(handle)
	except (OSError, tomllib.TOMLDecodeError) as error:
		problems.append(f'{path}: {error}')
		return {}


def mergeConfig(target: dict[str, Any], source: dict[str, Any], problems: list[str], prefix: str = '') -> None:
	'''Merge only known configuration keys, replacing arrays as whole values.'''
	for key, value in source.items():
		full_key = f'{prefix}.{key}' if prefix else key
		if key not in target:
			problems.append(f'unknown key: {full_key}')
		elif isinstance(target[key], dict) and isinstance(value, dict):
			mergeConfig(target[key], value, problems, full_key)
		else:
			target[key] = value


def validateConfig(data: dict[str, Any], problems: list[str]) -> Settings | None:
	'''Validate every schema field and cross-field invariant.'''
	for section, fields in SCHEMA.items():
		if section not in data:
			problems.append(f'missing section: {section}')
			continue
		if not isinstance(data[section], dict):
			problems.append(f'{section}: expected table')
			continue
		for key in fields:
			if key not in data[section]:
				problems.append(f'missing key: {section}.{key}')
		for key in data[section].keys() - fields.keys():
			problems.append(f'unknown key: {section}.{key}')
	for section in data.keys() - SCHEMA.keys():
		problems.append(f'unknown section: {section}')
	for section, fields in SCHEMA.items():
		if not isinstance(data.get(section), dict):
			continue
		for key, (kind, low, high) in fields.items():
			if key not in data[section]:
				continue
			value = data[section][key]
			name = f'{section}.{key}'
			valid = True
			if kind == 'int':
				valid = type(value) is int and low <= value <= high
			elif kind == 'float':
				valid = type(value) in (int, float) and low <= value <= high
			elif kind == 'str':
				valid = isinstance(value, str) and bool(value)
			elif kind == 'filename':
				valid = isinstance(value, str) and bool(value) and value not in ('.', '..') and '/' not in value and '\\' not in value
			elif kind == 'bool':
				valid = type(value) is bool
			elif kind == 'choice':
				valid = value in low
			elif kind == 'strings':
				valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
			elif kind == 'users':
				valid = isinstance(value, list) and all(isinstance(item, dict) and set(item) == {'username', 'role'} for item in value)
			if not valid:
				problems.append(f'{name}: invalid type or value')
	if problems:
		return None
	for name, value in [('paths.data_dir', data['paths']['data_dir']), ('paths.bootstrap_dir', data['paths']['bootstrap_dir']), ('agent.ca_bundle', data['agent']['ca_bundle']), ('agent.state_dir', data['agent']['state_dir']), ('agent.bootstrap_dir', data['agent']['bootstrap_dir']), ('agent.sim_root', data['agent']['sim_root']), ('agent.disk_usage_path', data['agent']['disk_usage_path'])]:
		if not Path(value).is_absolute():
			problems.append(f'{name}: must be an absolute path')
	if data['agent']['bootstrap_dir'] != data['paths']['bootstrap_dir']:
		problems.append('agent.bootstrap_dir: must match paths.bootstrap_dir')
	if not data['agent']['server_url'].startswith('https://'):
		problems.append('agent.server_url: must start with https://')
	if data['auth']['scrypt_n'] & (data['auth']['scrypt_n'] - 1):
		problems.append('auth.scrypt_n: must be a power of two')
	if data['agents']['online_threshold_s'] < 2 * data['agents']['heartbeat_interval_s']:
		problems.append('agents.online_threshold_s: must be at least twice heartbeat_interval_s')
	if data['actions']['dispatch_expiry_s'] < data['agent']['action_timeout_s'] + data['agents']['heartbeat_interval_s']:
		problems.append('actions.dispatch_expiry_s: too short for action timeout and heartbeat')
	if data['codes']['length'] < 12 or data['codes']['length'] % data['codes']['group_size']:
		problems.append('codes.length: must be at least 12 and divisible by group_size')
	if data['actions']['history_default_limit'] > data['actions']['history_max_limit']:
		problems.append('actions.history_default_limit: exceeds history_max_limit')
	if data['agent']['backoff_initial_s'] > data['agent']['backoff_max_s']:
		problems.append('agent.backoff_initial_s: exceeds backoff_max_s')
	if not data['actions']['enabled_kinds'] or not set(data['actions']['enabled_kinds']) <= ACTION_KINDS:
		problems.append('actions.enabled_kinds: must be a non-empty subset of ACTION_KINDS')
	users = data['seed']['users']
	usernames = [item.get('username') for item in users]
	roles = [item.get('role') for item in users]
	if len([str(name).lower() for name in usernames]) != len({str(name).lower() for name in usernames}) or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', name) for name in usernames):
		problems.append('seed.users: usernames must be unique valid names')
	if any(not isinstance(role, str) for role in roles) or not {'admin', 'operator'} <= {role for role in roles if isinstance(role, str)} or any(role not in ('admin', 'operator') for role in roles):
		problems.append('seed.users: at least one admin and one operator are required')
	agent_names = data['seed']['bootstrap_agent_names']
	if len([name.lower() for name in agent_names]) != len({name.lower() for name in agent_names}) or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name) or len(name) > data['agents']['name_max_length'] for name in agent_names):
		problems.append('seed.bootstrap_agent_names: names must be unique valid agent names')
	if problems:
		return None
	seed_data = dict(data['seed'])
	seed_data['users'] = tuple(SeedUser(**item) for item in users)
	seed_data['bootstrap_agent_names'] = tuple(agent_names)
	for section in ('actions', 'seed'):
		data[section] = dict(data[section])
	data['actions']['enabled_kinds'] = tuple(data['actions']['enabled_kinds'])
	data['seed']['bootstrap_agent_names'] = tuple(data['seed']['bootstrap_agent_names'])
	sections = {}
	classes = {'paths': PathsSettings, 'logging': LoggingSettings, 'database': DatabaseSettings, 'auth': AuthSettings, 'throttle': ThrottleSettings, 'limits': LimitsSettings, 'codes': CodesSettings, 'agents': AgentsSettings, 'actions': ActionsSettings, 'agent': AgentSettings}
	for section, cls in classes.items():
		sections[section] = cls(**data[section])
	sections['seed'] = SeedSettings(**seed_data)
	return Settings(**sections)


def loadSettings(config_path: str | Path | None = None, overlay_path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
	'''Load, merge and validate the complete configuration.'''
	problems: list[str] = []
	base_path = config_path or os.environ.get('LAB_CONFIG_FILE') or Path(__file__).resolve().parents[1] / 'config_settings.toml'
	data = readToml(base_path, problems)
	if overlay_path is None:
		overlay_path = os.environ.get('LAB_CONFIG_OVERLAY')
	if overlay_path:
		mergeConfig(data, readToml(overlay_path, problems), problems)
	if overrides:
		mergeConfig(data, overrides, problems)
	settings = validateConfig(data, problems)
	if problems or settings is None:
		raise ConfigError('Configuration errors:\n- ' + '\n- '.join(problems))
	return settings
