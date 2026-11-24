'''Command-line entry points for safe SQLite backup and restore.'''

import argparse
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from shared.config import loadSettings

EXPECTED_SCHEMA_VERSION = '1'


def backupDatabase(output_path: Path, database_path: Path) -> None:
	'''Create a consistent online backup of the live SQLite database.'''
	output_path.parent.mkdir(parents=True, exist_ok=True)
	if output_path.resolve() == database_path.resolve():
		raise ValueError('Backup output must not be the live database')
	with closing(sqlite3.connect(database_path)) as source, closing(sqlite3.connect(output_path)) as destination:
		source.backup(destination)
		destination.execute('PRAGMA journal_mode = DELETE')
		result = destination.execute('PRAGMA integrity_check').fetchone()
		if result is None or result[0] != 'ok':
			raise sqlite3.DatabaseError('Backup integrity check failed')


def validateDatabase(database_path: Path) -> None:
	'''Reject unreadable, inconsistent or incompatible SQLite database files.'''
	try:
		with closing(sqlite3.connect(database_path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
			integrity = connection.execute('PRAGMA integrity_check').fetchone()
			if integrity is None or integrity[0] != 'ok':
				raise ValueError('Database integrity check failed')
			version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
			if version is None or version[0] != EXPECTED_SCHEMA_VERSION:
				raise ValueError('Unsupported or missing schema_version')
	except (sqlite3.Error, OSError) as error:
		raise ValueError(f'Invalid SQLite backup: {error}') from error


def restoreDatabase(input_path: Path, database_path: Path, data_dir: Path) -> None:
	'''Validate a backup and atomically replace the live database file.'''
	if not input_path.is_file():
		raise ValueError(f'Backup file does not exist: {input_path}')
	if input_path.resolve() == database_path.resolve():
		raise ValueError('Restore input must not be the live database')
	data_dir.mkdir(parents=True, exist_ok=True)
	file_descriptor, temporary_name = tempfile.mkstemp(prefix='.restore-', suffix='.db', dir=data_dir)
	temporary_path = Path(temporary_name)
	try:
		with os.fdopen(file_descriptor, 'wb') as destination, input_path.open('rb') as source:
			while chunk := source.read(1024 * 1024):
				destination.write(chunk)
			destination.flush()
			os.fsync(destination.fileno())
		validateDatabase(temporary_path)
		os.replace(temporary_path, database_path)
		Path(f'{database_path}-wal').unlink(missing_ok=True)
		Path(f'{database_path}-shm').unlink(missing_ok=True)
	finally:
		temporary_path.unlink(missing_ok=True)


def main() -> int:
	'''Parse and execute the backup or restore command.'''
	parser = argparse.ArgumentParser(prog='python -m app.cli')
	subparsers = parser.add_subparsers(dest='command', required=True)
	backup_parser = subparsers.add_parser('backup', help='create an online SQLite backup')
	backup_parser.add_argument('output', type=Path)
	restore_parser = subparsers.add_parser('restore', help='validate and replace the database')
	restore_parser.add_argument('input', type=Path)
	arguments = parser.parse_args()
	settings = loadSettings()
	data_dir = Path(settings.paths.data_dir)
	database_path = data_dir / settings.paths.database_file
	try:
		if arguments.command == 'backup':
			backupDatabase(arguments.output, database_path)
		else:
			restoreDatabase(arguments.input, database_path, data_dir)
	except (OSError, sqlite3.Error, ValueError) as error:
		print(f'Error: {error}', file=sys.stderr)
		return 1
	print(f'{arguments.command.capitalize()} completed successfully')
	return 0


if __name__ == '__main__':
	sys.exit(main())
