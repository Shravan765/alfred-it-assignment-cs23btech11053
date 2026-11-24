'''SQLite persistence, connection policy and concurrency tests.'''

import sqlite3
import threading

import pytest

from server.app.db import getConnection, initSchema, runInTransaction


def testSchemaInitIsIdempotent(temp_settings):
	'''Initialise schema repeatedly while retaining a single schema version.'''
	connection = getConnection(temp_settings)
	initSchema(connection)
	initSchema(connection)
	assert connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '1'
	assert connection.execute("SELECT value FROM meta WHERE key='boot_count'").fetchone()[0] == '2'
	connection.close()


def testForeignKeysAreEnforced(temp_settings):
	'''Reject orphan rows and cascade session deletion with its user.'''
	connection = getConnection(temp_settings)
	initSchema(connection)
	with pytest.raises(sqlite3.IntegrityError):
		connection.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES('x',999,0,1)")
	connection.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES('test','hash','admin',0)")
	connection.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES('y',1,0,1)")
	connection.execute('DELETE FROM users WHERE id=1')
	assert connection.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
	connection.close()


@pytest.mark.timeout(10)
def testConcurrentWritesDoNotLock(temp_settings):
	'''Serialise concurrent writers with SQLite busy timeout and transactions.'''
	connection = getConnection(temp_settings)
	initSchema(connection)
	connection.close()
	errors = []
	def insertRow(index):
		'''Insert a distinct user from one worker thread.'''
		try:
			worker = getConnection(temp_settings)
			with runInTransaction(worker):
				worker.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)', (f'user{index}', 'hash', 'operator', 0))
			worker.close()
		except Exception as error:  # noqa: BLE001
			errors.append(error)
	threads = [threading.Thread(target=insertRow, args=(index,)) for index in range(8)]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join()
	assert errors == []
	worker = getConnection(temp_settings)
	assert worker.execute('SELECT count(*) FROM users').fetchone()[0] == 8
	worker.close()


def testConnectionPragmasUseSettings(temp_settings):
	'''Apply configured journal mode, busy timeout and foreign-key policy.'''
	connection = getConnection(temp_settings)
	assert connection.execute('PRAGMA busy_timeout').fetchone()[0] == temp_settings.database.busy_timeout_ms
	assert connection.execute('PRAGMA journal_mode').fetchone()[0].lower() == temp_settings.database.journal_mode.lower()
	assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
	connection.close()
