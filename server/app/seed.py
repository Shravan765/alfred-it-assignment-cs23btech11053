'''Idempotent sample-user creation and private credential-file management.'''

import secrets
import string
from pathlib import Path

from server.app.clock import nowEpoch
from server.app.db import runInTransaction
from server.app.security import hashPassword


def seedUsers(settings, connection, now: int | None = None) -> None:
	'''Create missing configured users without changing existing credentials.'''
	created_at = nowEpoch() if now is None else now
	credentials_path = Path(settings.paths.data_dir) / settings.paths.credentials_file
	credentials_path.parent.mkdir(parents=True, exist_ok=True)
	new_credentials = []
	with runInTransaction(connection):
		for user in settings.seed.users:
			existing = connection.execute('SELECT id FROM users WHERE username=? COLLATE NOCASE', (user.username,)).fetchone()
			if existing:
				continue
			password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(settings.seed.password_length))
			connection.execute('INSERT INTO users(username, password_hash, role, created_at) VALUES(?, ?, ?, ?)', (user.username, hashPassword(password, settings), user.role, created_at))
			new_credentials.append(f'{user.username}:{password}')
	if new_credentials and not credentials_path.exists():
		with credentials_path.open('x', encoding='utf-8') as handle:
			handle.write('\n'.join(new_credentials) + '\n')
		credentials_path.chmod(0o600)
