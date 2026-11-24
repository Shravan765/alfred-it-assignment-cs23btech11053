'''Tests for complete configuration loading, merging and validation.'''

import re
import sys
import tomllib
from dataclasses import fields
from pathlib import Path

import pytest

from shared.config import SCHEMA, ConfigError, loadSettings

ROOT = Path(__file__).resolve().parents[2]
UNIMPLEMENTED_KEYS = {
	'paths.bootstrap_dir', 'throttle.login_max_failures', 'throttle.login_window_s',
	'throttle.register_max_failures', 'throttle.register_window_s', 'limits.max_body_bytes',
	'codes.ttl_s', 'codes.length', 'codes.group_size', 'codes.max_active',
	'agents.heartbeat_interval_s', 'agents.online_threshold_s', 'agents.name_max_length', 'agents.info_max_bytes',
	'actions.enabled_kinds', 'actions.max_queued_per_agent', 'actions.claim_batch_size', 'actions.dispatch_expiry_s',
	'actions.queued_expiry_s', 'actions.output_max_bytes', 'actions.history_default_limit', 'actions.history_max_limit',
	'agent.server_url', 'agent.ca_bundle', 'agent.state_dir', 'agent.bootstrap_dir', 'agent.connect_timeout_s',
	'agent.read_timeout_s', 'agent.backoff_initial_s', 'agent.backoff_max_s', 'agent.backoff_jitter',
	'agent.action_timeout_s', 'agent.sim_root', 'agent.demo_spool_jobs', 'agent.disk_usage_path',
	'seed.bootstrap_agent_names'
}


def testShippedConfigMatchesDataclasses():
	'''Assert every TOML key has exactly one typed dataclass field.'''
	settings = loadSettings(ROOT / 'config_settings.toml')
	parsed = tomllib.loads((ROOT / 'config_settings.toml').read_text())
	for section, values in parsed.items():
		assert set(values) == {field.name for field in fields(getattr(settings, section))}
	assert set(parsed) == set(SCHEMA)


def testConfigMissingUnknownAndWrongType(tmp_path):
	'''Report missing, unknown and mistyped values with their full key names.'''
	text = (ROOT / 'config_settings.toml').read_text()
	text = text.replace('level = "INFO"', 'typo = "INFO"', 1)
	text = text.replace('busy_timeout_ms = 5000', 'busy_timeout_ms = "slow"', 1)
	path = tmp_path / 'bad.toml'
	path.write_text(text)
	with pytest.raises(ConfigError) as error:
		loadSettings(path)
	assert 'logging.level' in str(error.value)
	assert 'logging.typo' in str(error.value)
	assert 'database.busy_timeout_ms' in str(error.value)



def testOverridesMergeAndArrayReplacement(tmp_path, monkeypatch):
	'''Apply overrides after overlay and replace arrays instead of merging them.'''
	overlay = tmp_path / 'overlay.toml'
	overlay.write_text('[logging]\nlevel = "DEBUG"\n')
	settings = loadSettings(overlay_path=overlay, overrides={'logging': {'level': 'ERROR'}})
	assert settings.logging.level == 'ERROR'
	monkeypatch.setenv('LAB_CONFIG_OVERLAY', str(overlay))
	assert loadSettings().logging.level == 'DEBUG'
	monkeypatch.delenv('LAB_CONFIG_OVERLAY')
	changed = loadSettings(overrides={'seed': {'bootstrap_agent_names': ['lab-01']}})
	assert changed.seed.bootstrap_agent_names == ('lab-01',)
	with pytest.raises(ConfigError, match='unknown key'):
		loadSettings(overrides={'logging': {'typo': True}})
	overlay.write_text('[logging]\nlevel = \"DEBUG\"\ntypo = true\n')
	with pytest.raises(ConfigError, match='logging.typo'):
		loadSettings(overlay_path=overlay)


def testCrossFieldRules(settings):
	'''Reject every important cross-field invariant.'''
	invalid = [
		{'agents': {'online_threshold_s': settings.agents.heartbeat_interval_s}},
		{'actions': {'dispatch_expiry_s': settings.agent.action_timeout_s + settings.agents.heartbeat_interval_s - 1}},
		{'codes': {'length': settings.codes.group_size * 3 + 1}},
		{'actions': {'history_default_limit': settings.actions.history_max_limit + 1}},
		{'auth': {'scrypt_n': settings.auth.scrypt_n + 1}},
		{'agent': {'backoff_initial_s': settings.agent.backoff_max_s + 1}},
		{'actions': {'enabled_kinds': ['shell_exec']}},
		{'agent': {'server_url': 'http://localhost'}},
		{'database': {'busy_timeout_ms': 0}},		
	]
	for override in invalid:
		with pytest.raises(ConfigError):
			loadSettings(overrides=override)


def testEnvironmentPathAndMissingFile(monkeypatch, tmp_path):
	'''Honor LAB_CONFIG_FILE and report a missing file clearly.'''
	monkeypatch.setenv('LAB_CONFIG_FILE', str(ROOT / 'config_settings.toml'))
	assert loadSettings().logging.level == 'INFO'
	monkeypatch.setenv('LAB_CONFIG_FILE', str(tmp_path / 'missing.toml'))
	with pytest.raises(ConfigError, match='missing.toml'):
		loadSettings()


def testNoSecretDefaultsAndPythonVersion():
	'''Keep secrets out of string defaults and require the tomllib runtime.'''
	data = tomllib.loads((ROOT / 'config_settings.toml').read_text())
	assert sys.version_info >= (3, 11)
	for values in data.values():
		for key, value in values.items():
			if isinstance(value, str):
				assert not any(secret in key.lower() for secret in ('password', 'secret', 'token'))


def testConfigKeyRatchet():
	'''Ensure each config key is referenced by implementation or explicitly deferred.'''
	keys = {f'{section}.{key}' for section, values in SCHEMA.items() for key in values}
	source = '\n'.join(path.read_text() for path in ROOT.rglob('*.py') if 'tests' not in path.parts and path.name != 'config.py')
	used = {key for key in keys if re.search(rf'\b(?:settings|resolved_settings)\.{re.escape(key)}\b', source)}
	assert UNIMPLEMENTED_KEYS <= keys
	assert not (used & UNIMPLEMENTED_KEYS)
	assert keys == used | UNIMPLEMENTED_KEYS
