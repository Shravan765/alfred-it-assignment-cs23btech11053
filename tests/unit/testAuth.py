'''User password, session, seeding and authorization tests.'''

from pathlib import Path

from fastapi.testclient import TestClient

from server.app.db import getConnection, initSchema
from server.app.main import createApp
from server.app.security import hashPassword, hashToken, verifyPassword
from server.app.seed import seedUsers


def testPasswordHashRoundTripAndParameters(settings):
	'''Hash and verify passwords using the configured scrypt cost and fresh salts.'''
	first = hashPassword('correct horse battery staple', settings)
	second = hashPassword('correct horse battery staple', settings)
	assert verifyPassword('correct horse battery staple', first)
	assert not verifyPassword('wrong', first)
	assert first.split('$')[1:4] == [str(settings.auth.scrypt_n), str(settings.auth.scrypt_r), str(settings.auth.scrypt_p)]
	assert first != second


def testSeedUsersIdempotentAndPrivateFile(temp_settings):
	'''Seed configured users once and preserve credentials across repeated runs.'''
	connection = getConnection(temp_settings)
	initSchema(connection)
	seedUsers(temp_settings, connection)
	path = Path(temp_settings.paths.data_dir) / temp_settings.paths.credentials_file
	original = path.read_text()
	assert path.stat().st_mode & 0o777 == 0o600
	assert len(original.strip().splitlines()) == len(temp_settings.seed.users)
	password_hash = connection.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
	seedUsers(temp_settings, connection)
	assert path.read_text() == original
	assert connection.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0] == password_hash
	connection.close()


def testLoginMeLogoutAndExpiry(temp_settings, fake_clock):
	'''Issue a hashed session, expose identity, expire it at the exact boundary and revoke it.'''
	connection = getConnection(temp_settings)
	initSchema(connection)
	seedUsers(temp_settings, connection)
	connection.close()
	app = createApp(temp_settings, clock=fake_clock)
	with TestClient(app) as client:
		credentials = dict(line.split(':', 1) for line in (Path(temp_settings.paths.data_dir) / temp_settings.paths.credentials_file).read_text().splitlines())
		login = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']})
		assert login.status_code == 200
		token = login.json()['access_token']
		connection = getConnection(temp_settings)
		assert connection.execute('SELECT token_hash FROM sessions').fetchone()[0] == hashToken(token)
		assert token not in Path(temp_settings.paths.data_dir, temp_settings.paths.database_file).read_bytes().decode('latin1', errors='ignore')
		connection.close()
		headers = {'Authorization': f'Bearer {token}'}
		assert client.get('/api/auth/me', headers=headers).json()['role'] == 'admin'
		fake_clock.advance(temp_settings.auth.session_ttl_s)
		assert client.get('/api/auth/me', headers=headers).status_code == 401
		fake_clock.value -= temp_settings.auth.session_ttl_s
		assert client.post('/api/auth/logout', headers=headers).status_code == 200
		assert client.get('/api/auth/me', headers=headers).status_code == 401


def testUnknownAndWrongPasswordHaveSameResponse(temp_settings):
	'''Use a dummy scrypt verification and identical public errors for unknown users.'''
	connection = getConnection(temp_settings)
	initSchema(connection)
	seedUsers(temp_settings, connection)
	connection.close()
	with TestClient(createApp(temp_settings)) as client:
		unknown = client.post('/api/auth/login', json={'username': 'missing', 'password': 'wrong-password'})
		wrong = client.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong-password'})
		assert unknown.status_code == wrong.status_code == 401
		assert unknown.json() == wrong.json()


def testConfiguredSeedUserIsCreated(temp_settings):
	'''Create an additional user introduced only through the config override.'''
	from shared.config import loadSettings
	changed = loadSettings(overrides={'paths': {'data_dir': temp_settings.paths.data_dir}, 'seed': {'users': [{'username': 'admin', 'role': 'admin'}, {'username': 'operator', 'role': 'operator'}, {'username': 'qa_user', 'role': 'operator'}]}})
	connection = getConnection(changed)
	initSchema(connection)
	seedUsers(changed, connection)
	assert connection.execute("SELECT role FROM users WHERE username='qa_user'").fetchone()[0] == 'operator'
	connection.close()


def testCapabilityRoleMatrix(temp_settings):
	'''Enforce admin/operator capability differences through one policy table.'''
	from typing import Annotated, Any

	from fastapi import Depends

	from server.app.permissions import CAPABILITIES, requireCapability
	settings = temp_settings
	app = createApp(settings)
	manage_codes = requireCapability('manage_codes')

	@app.get('/api/probe/admin')
	def adminProbe(user: Annotated[Any, Depends(manage_codes)]):
		'''Return a protected resource to verify role authorization.'''
		return {'role': user['role']}
	with TestClient(app) as client:
		credentials_path = Path(settings.paths.data_dir) / settings.paths.credentials_file
		credentials = dict(line.split(':', 1) for line in credentials_path.read_text().splitlines())
		assert client.get('/api/probe/admin').status_code == 401
		# The test client startup seeds users into the configured temporary database.
		for username in ('admin', 'operator'):
			if username not in credentials:
				continue
			response = client.post('/api/auth/login', json={'username': username, 'password': credentials[username]})
			assert response.status_code == 200
			result = client.get('/api/probe/admin', headers={'Authorization': f"Bearer {response.json()['access_token']}"})
			assert result.status_code == (200 if username == 'admin' else 403)
	assert CAPABILITIES['view_agents'] == {'admin', 'operator'}
	assert CAPABILITIES['manage_codes'] == {'admin'}



def testRouteAuthorizationMatrix(temp_settings):
	'''Cover every registered route for anonymous, operator, admin and agent principals.'''
	app = createApp(temp_settings)
	route_matrix = {
		('GET', '/health'): {'anonymous': 200, 'operator': 200, 'admin': 200, 'agent': 200},
		('POST', '/api/auth/login'): {'anonymous': 422, 'operator': 422, 'admin': 422, 'agent': 422},
		('GET', '/api/auth/me'): {'anonymous': 401, 'operator': 200, 'admin': 200, 'agent': 401},
		('POST', '/api/auth/logout'): {'anonymous': 401, 'operator': 200, 'admin': 200, 'agent': 401},
		('POST', '/api/admin/registration-codes'): {'anonymous': 401, 'operator': 403, 'admin': 201, 'agent': 401},
		('GET', '/api/admin/registration-codes'): {'anonymous': 401, 'operator': 403, 'admin': 200, 'agent': 401},
		('DELETE', '/api/admin/registration-codes/{code_id}'): {'anonymous': 401, 'operator': 403, 'admin': 404, 'agent': 401},
		('POST', '/api/agent/register'): {'anonymous': 422, 'operator': 422, 'admin': 422, 'agent': 422},
	}
	with TestClient(app) as client:
		credentials_path = Path(temp_settings.paths.data_dir) / temp_settings.paths.credentials_file
		credentials = dict(line.split(':', 1) for line in credentials_path.read_text().splitlines())
		# A genuinely registered agent, so the agent column tests a real agent token.
		setup_admin_token = client.post('/api/auth/login', json={'username': 'admin', 'password': credentials['admin']}).json()['access_token']
		code = client.post('/api/admin/registration-codes', headers={'Authorization': f'Bearer {setup_admin_token}'}).json()['code']
		agent_token = client.post('/api/agent/register', json={'code': code, 'name': 'matrix-agent'}).json()['token']

		def principalHeaders(role: str) -> dict[str, str]:
			'''Return active authorization headers for the principal under test.'''
			if role == 'anonymous':
				return {}
			if role == 'agent':
				return {'Authorization': f'Bearer {agent_token}'}
			response = client.post('/api/auth/login', json={'username': role, 'password': credentials[role]})
			return {'Authorization': f"Bearer {response.json()['access_token']}"}

		registered = {(m, getattr(ctx, 'path', '')) for r in app.routes for ctx in (getattr(r, 'effective_route_contexts', lambda target=r: [target])()) for m in getattr(ctx, 'methods', set()) if m in {'GET', 'POST', 'DELETE'}}
		assert registered == set(route_matrix)
		for (method, path), expected in route_matrix.items():
			for principal in ('anonymous', 'operator', 'admin', 'agent'):
				headers = principalHeaders(principal)
				response = client.request(method, path.replace('{code_id}', '999999'), headers=headers)
				assert response.status_code == expected[principal], (method, path, principal)
