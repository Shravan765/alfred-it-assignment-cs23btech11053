'''SQLite connection, schema and explicit transaction helpers.'''

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from shared.config import Settings


def getConnection(settings: Settings) -> sqlite3.Connection:
	'''Open a configured SQLite connection with foreign keys enabled.'''
	Path(settings.paths.data_dir).mkdir(parents=True, exist_ok=True)
	database_path = Path(settings.paths.data_dir) / settings.paths.database_file
	connection = sqlite3.connect(database_path, timeout=settings.database.busy_timeout_ms / 1000, isolation_level=None, check_same_thread=False)
	connection.row_factory = sqlite3.Row
	connection.execute('PRAGMA foreign_keys = ON')
	connection.execute(f'PRAGMA busy_timeout = {settings.database.busy_timeout_ms}')
	connection.execute(f'PRAGMA journal_mode = {settings.database.journal_mode}')
	return connection


def initSchema(connection: sqlite3.Connection) -> None:
	'''Create the schema idempotently and increment the persistent boot count.'''
	if sqlite3.sqlite_version_info < (3, 35, 0):
		raise RuntimeError('SQLite 3.35 or newer is required')
	schema_path = Path(__file__).with_name('schema.sql')
	connection.executescript(schema_path.read_text(encoding='utf-8'))
	with runInTransaction(connection):
		connection.execute("INSERT INTO meta(key, value) VALUES('schema_version', '1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
		connection.execute("INSERT INTO meta(key, value) VALUES('boot_count', '0') ON CONFLICT(key) DO NOTHING")
		connection.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='boot_count'")


@contextmanager
def runInTransaction(connection: sqlite3.Connection):
	'''Run a write operation under an immediate SQLite transaction.'''
	connection.execute('BEGIN IMMEDIATE')
	try:
		yield connection
		connection.execute('COMMIT')
	except Exception:
		connection.execute('ROLLBACK')
		raise


def dbConn(settings: Settings):
	'''Yield a request-scoped database connection and always close it.'''
	connection = getConnection(settings)
	try:
		yield connection
	finally:
		connection.close()
