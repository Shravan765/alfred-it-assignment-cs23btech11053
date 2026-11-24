'''Backup and restore validation, consistency and atomicity tests.'''

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from server.app.cli import backupDatabase, restoreDatabase
from server.app.db import getConnection, initSchema, runInTransaction


def testBackupRestoreRoundTrip(temp_settings, tmp_path):
	'''Restore a valid backup and recover the exact database marker.'''
	connection = getConnection(temp_settings)
	initSchema(connection)
	connection.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES('before','hash','admin',1)")
	connection.close()
	database_path = Path(temp_settings.paths.data_dir) / temp_settings.paths.database_file
	backup_path = tmp_path / 'backup.db'
	backupDatabase(backup_path, database_path)
	connection = getConnection(temp_settings)
	connection.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES('after','hash','operator',2)")
	connection.close()
	restoreDatabase(backup_path, database_path, Path(temp_settings.paths.data_dir))
	connection = getConnection(temp_settings)
	users = [row[0] for row in connection.execute('SELECT username FROM users ORDER BY username')]
	connection.close()
	assert users == ['before']


@pytest.mark.timeout(15)
def testOnlineBackupDuringWritesIsConsistent(temp_settings, tmp_path):
	'''Take an online backup while a separate thread commits inserted rows.'''
	connection = getConnection(temp_settings)
	initSchema(connection)
	connection.close()
	started = threading.Event()
	finished = threading.Event()
	errors = []

	def writer():
		'''Commit a sequence of independent rows until the test signals stop.'''
		worker = getConnection(temp_settings)
		try:
			for index in range(200):
				with runInTransaction(worker):
					worker.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)', (f'writer{index}', 'hash', 'operator', index))
					started.set()
				time.sleep(0.001)
				if finished.is_set():
					break
		except Exception as error:  # noqa: BLE001
			errors.append(error)
		finally:
			worker.close()

	thread = threading.Thread(target=writer)
	thread.start()
	assert started.wait(5)
	database_path = Path(temp_settings.paths.data_dir) / temp_settings.paths.database_file
	backup_path = tmp_path / 'during-write.db'
	backupDatabase(backup_path, database_path)
	finished.set()
	thread.join(5)
	assert not thread.is_alive()
	assert errors == []
	with sqlite3.connect(backup_path) as backup:
		assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
		assert backup.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '1'


@pytest.mark.parametrize('invalid_kind', ['text', 'truncated', 'wrong-version'])
def testInvalidRestoreLeavesLiveDatabaseUntouched(temp_settings, tmp_path, invalid_kind):
	'''Reject malformed or incompatible backups without changing live data.'''
	connection = getConnection(temp_settings)
	initSchema(connection)
	connection.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES('preserve','hash','admin',1)")
	connection.close()
	database_path = Path(temp_settings.paths.data_dir) / temp_settings.paths.database_file
	backup_path = tmp_path / 'invalid.db'
	if invalid_kind == 'text':
		backup_path.write_text('not a database', encoding='utf-8')
	else:
		backupDatabase(backup_path, database_path)
		if invalid_kind == 'truncated':
			backup_path.write_bytes(backup_path.read_bytes()[:100])
		else:
			with sqlite3.connect(backup_path) as backup:
				backup.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
	with pytest.raises(ValueError):
		restoreDatabase(backup_path, database_path, Path(temp_settings.paths.data_dir))
	connection = getConnection(temp_settings)
	assert connection.execute('SELECT username FROM users').fetchone()[0] == 'preserve'
	connection.close()
