'''Unit and integration tests for agent heartbeat, online calculation and operator agent views.'''

import json
from pathlib import Path

from fastapi.testclient import TestClient

from server.app.clock import toIso
from server.app.main import createApp
from server.app.routers.agents import isOnline
from shared.config import loadSettings


def testShippedTimingDefaults():
	'''Verify the shipped config contains the required 30s heartbeat and 90s online threshold.'''
	settings = loadSettings()
	assert settings.agents.heartbeat_interval_s == 30
	assert settings.agents.online_threshold_s == 90


def registerTestAgent(client: TestClient, admin_token: str, agent_name: str) -> tuple[int, str]:
	'''Helper to create a registration code and register an agent.'''
	code_response = client.post('/api/admin/registration-codes', headers={'Authorization': f'Bearer {admin_token}'})
	assert code_response.status_code == 201
	code = code_response.json()['code']
	register_response = client.post('/api/agent/register', json={'code': code, 'name': agent_name})
	assert register_response.status_code == 201
	data = register_response.json()
	return data['agent_id'], data['token']


def getSampleCredentials(data_dir: str, filename: str) -> dict[str, str]:
	'''Read generated sample credentials for admin and operator.'''
	path = Path(data_dir) / filename
	return dict(line.split(':', 1) for line in path.read_text().splitlines())


def testHeartbeatSuccess(temp_settings):
	'''Verify a registered agent can send a heartbeat and receives the configured interval.'''
	app = createApp(temp_settings)
	with TestClient(app) as client:
		creds = getSampleCredentials(temp_settings.paths.data_dir, temp_settings.paths.credentials_file)
		admin_token = client.post('/api/auth/login', json={'username': 'admin', 'password': creds['admin']}).json()['access_token']
		_, token = registerTestAgent(client, admin_token, 'heartbeat-agent')

		response = client.post('/api/agent/heartbeat', headers={'Authorization': f'Bearer {token}'}, json={'info': {'os': 'linux'}})
		assert response.status_code == 200
		body = response.json()
		assert body['interval_s'] == temp_settings.agents.heartbeat_interval_s
		assert body['actions'] == []
		assert body['server_time'].endswith('Z')


def testHeartbeatUpdatesDatabase(temp_settings):
	'''Verify that the heartbeat updates last_seen_at, last_ip and info_json.'''
	fake_now = 1700000000
	app = createApp(temp_settings, clock=lambda: fake_now)
	with TestClient(app) as client:
		creds = getSampleCredentials(temp_settings.paths.data_dir, temp_settings.paths.credentials_file)
		admin_token = client.post('/api/auth/login', json={'username': 'admin', 'password': creds['admin']}).json()['access_token']
		agent_id, token = registerTestAgent(client, admin_token, 'db-agent')

		client.post('/api/agent/heartbeat', headers={'Authorization': f'Bearer {token}'}, json={'info': {'hostname': 'lab-pc-1'}})
		conn = app.state.connection_factory()
		try:
			row = conn.execute('SELECT last_seen_at, last_ip, info_json FROM agents WHERE id=?', (agent_id,)).fetchone()
			assert row['last_seen_at'] == fake_now
			assert row['last_ip'] is not None
			assert json.loads(row['info_json']) == {'hostname': 'lab-pc-1'}
		finally:
			conn.close()


def testHeartbeatRejectsUnknownOrRemovedToken(temp_settings):
	'''Verify that unauthenticated or revoked agent tokens return HTTP 401.'''
	app = createApp(temp_settings)
	with TestClient(app) as client:
		response = client.post('/api/agent/heartbeat', headers={'Authorization': 'Bearer non-existent-token'})
		assert response.status_code == 401

		creds = getSampleCredentials(temp_settings.paths.data_dir, temp_settings.paths.credentials_file)
		admin_token = client.post('/api/auth/login', json={'username': 'admin', 'password': creds['admin']}).json()['access_token']
		agent_id, token = registerTestAgent(client, admin_token, 'removed-agent')

		conn = app.state.connection_factory()
		try:
			conn.execute('DELETE FROM agents WHERE id=?', (agent_id,))
		finally:
			conn.close()

		response = client.post('/api/agent/heartbeat', headers={'Authorization': f'Bearer {token}'})
		assert response.status_code == 401


def testHeartbeatRejectsUserToken(temp_settings):
	'''Verify that user sessions cannot authenticate on agent heartbeat endpoint.'''
	app = createApp(temp_settings)
	with TestClient(app) as client:
		creds = getSampleCredentials(temp_settings.paths.data_dir, temp_settings.paths.credentials_file)
		operator_token = client.post('/api/auth/login', json={'username': 'operator', 'password': creds['operator']}).json()['access_token']
		response = client.post('/api/agent/heartbeat', headers={'Authorization': f'Bearer {operator_token}'})
		assert response.status_code == 401


def testHeartbeatInfoMaxBytesExceeded(temp_settings):
	'''Verify that an info payload exceeding info_max_bytes is rejected with HTTP 413.'''
	app = createApp(temp_settings)
	with TestClient(app) as client:
		creds = getSampleCredentials(temp_settings.paths.data_dir, temp_settings.paths.credentials_file)
		admin_token = client.post('/api/auth/login', json={'username': 'admin', 'password': creds['admin']}).json()['access_token']
		_, token = registerTestAgent(client, admin_token, 'large-info-agent')

		large_payload = {'large_key': 'x' * (temp_settings.agents.info_max_bytes + 10)}
		response = client.post('/api/agent/heartbeat', headers={'Authorization': f'Bearer {token}'}, json={'info': large_payload})
		assert response.status_code == 413


def testHeartbeatIdentityOnlyFromToken(temp_settings):
	'''Verify that identity comes solely from bearer token and foreign IDs in body are ignored.'''
	app = createApp(temp_settings)
	with TestClient(app) as client:
		creds = getSampleCredentials(temp_settings.paths.data_dir, temp_settings.paths.credentials_file)
		admin_token = client.post('/api/auth/login', json={'username': 'admin', 'password': creds['admin']}).json()['access_token']
		agent_a_id, token_a = registerTestAgent(client, admin_token, 'agent-a')
		agent_b_id, _ = registerTestAgent(client, admin_token, 'agent-b')

		client.post('/api/agent/heartbeat', headers={'Authorization': f'Bearer {token_a}'}, json={'id': agent_b_id, 'name': 'agent-b', 'info': {'owner': 'a'}})

		conn = app.state.connection_factory()
		try:
			row_a = conn.execute('SELECT last_seen_at, info_json FROM agents WHERE id=?', (agent_a_id,)).fetchone()
			row_b = conn.execute('SELECT last_seen_at, info_json FROM agents WHERE id=?', (agent_b_id,)).fetchone()
			assert row_a['last_seen_at'] is not None
			assert json.loads(row_a['info_json']) == {'owner': 'a'}
			assert row_b['last_seen_at'] is None
		finally:
			conn.close()


def testIsOnlineBoundaryValues(temp_settings):
	'''Verify exact boundary conditions for online threshold calculation.'''
	threshold = temp_settings.agents.online_threshold_s
	now = 10000
	assert not isOnline(None, now, threshold)
	assert isOnline(now - (threshold - 1), now, threshold)
	assert isOnline(now - threshold, now, threshold)
	assert not isOnline(now - (threshold + 1), now, threshold)


def testListAgentsMixedFleet(temp_settings):
	'''Verify that the agent list displays correct online status across a mixed fleet.'''
	current_time = 1700000000
	threshold = temp_settings.agents.online_threshold_s
	app = createApp(temp_settings, clock=lambda: current_time)
	with TestClient(app) as client:
		creds = getSampleCredentials(temp_settings.paths.data_dir, temp_settings.paths.credentials_file)
		admin_token = client.post('/api/auth/login', json={'username': 'admin', 'password': creds['admin']}).json()['access_token']
		operator_token = client.post('/api/auth/login', json={'username': 'operator', 'password': creds['operator']}).json()['access_token']

		id_never, _ = registerTestAgent(client, admin_token, 'never-seen')
		id_recent, token_recent = registerTestAgent(client, admin_token, 'recently-seen')
		id_stale, _ = registerTestAgent(client, admin_token, 'stale-seen')

		client.post('/api/agent/heartbeat', headers={'Authorization': f'Bearer {token_recent}'})

		conn = app.state.connection_factory()
		try:
			conn.execute('UPDATE agents SET last_seen_at=? WHERE id=?', (current_time - (threshold + 5), id_stale))
		finally:
			conn.close()

		response = client.get('/api/agents', headers={'Authorization': f'Bearer {operator_token}'})
		assert response.status_code == 200
		agents_by_id = {item['id']: item for item in response.json()['agents']}

		assert not agents_by_id[id_never]['online']
		assert agents_by_id[id_never]['last_seen_at'] is None

		assert agents_by_id[id_recent]['online']
		assert agents_by_id[id_recent]['last_seen_at'] == toIso(current_time)

		assert not agents_by_id[id_stale]['online']


def testListAgentsAuthorization(temp_settings):
	'''Verify that list and detail endpoints require view_agents capability.'''
	app = createApp(temp_settings)
	with TestClient(app) as client:
		creds = getSampleCredentials(temp_settings.paths.data_dir, temp_settings.paths.credentials_file)
		admin_token = client.post('/api/auth/login', json={'username': 'admin', 'password': creds['admin']}).json()['access_token']
		operator_token = client.post('/api/auth/login', json={'username': 'operator', 'password': creds['operator']}).json()['access_token']
		_, agent_token = registerTestAgent(client, admin_token, 'auth-agent')

		assert client.get('/api/agents').status_code == 401
		assert client.get('/api/agents', headers={'Authorization': f'Bearer {agent_token}'}).status_code == 401
		assert client.get('/api/agents', headers={'Authorization': f'Bearer {operator_token}'}).status_code == 200
		assert client.get('/api/agents', headers={'Authorization': f'Bearer {admin_token}'}).status_code == 200


def testGetAgentDetail(temp_settings):
	'''Verify single agent detail retrieval returns valid structure or HTTP 404.'''
	app = createApp(temp_settings)
	with TestClient(app) as client:
		creds = getSampleCredentials(temp_settings.paths.data_dir, temp_settings.paths.credentials_file)
		admin_token = client.post('/api/auth/login', json={'username': 'admin', 'password': creds['admin']}).json()['access_token']
		operator_token = client.post('/api/auth/login', json={'username': 'operator', 'password': creds['operator']}).json()['access_token']
		agent_id, agent_token = registerTestAgent(client, admin_token, 'detail-agent')

		client.post('/api/agent/heartbeat', headers={'Authorization': f'Bearer {agent_token}'}, json={'info': {'disk': '10GB'}})

		response = client.get(f'/api/agents/{agent_id}', headers={'Authorization': f'Bearer {operator_token}'})
		assert response.status_code == 200
		body = response.json()
		assert body['id'] == agent_id
		assert body['name'] == 'detail-agent'
		assert body['info'] == {'disk': '10GB'}
		assert body['online']

		not_found = client.get('/api/agents/999999', headers={'Authorization': f'Bearer {operator_token}'})
		assert not_found.status_code == 404


def testHeartbeatIntegrationWorkflow(temp_settings):
	'''In-process integration test: register, heartbeat, operator verification and timeout.'''
	current_time = 1700000000
	threshold = temp_settings.agents.online_threshold_s

	def getClock():
		'''Return the current simulated epoch time.'''
		return current_time

	app = createApp(temp_settings, clock=getClock)
	with TestClient(app) as client:
		creds = getSampleCredentials(temp_settings.paths.data_dir, temp_settings.paths.credentials_file)
		admin_token = client.post('/api/auth/login', json={'username': 'admin', 'password': creds['admin']}).json()['access_token']
		operator_token = client.post('/api/auth/login', json={'username': 'operator', 'password': creds['operator']}).json()['access_token']

		agent_id, token = registerTestAgent(client, admin_token, 'workflow-agent')

		initial_view = client.get(f'/api/agents/{agent_id}', headers={'Authorization': f'Bearer {operator_token}'}).json()
		assert not initial_view['online']

		client.post('/api/agent/heartbeat', headers={'Authorization': f'Bearer {token}'}, json={'info': {'status': 'active'}})

		online_view = client.get(f'/api/agents/{agent_id}', headers={'Authorization': f'Bearer {operator_token}'}).json()
		assert online_view['online']
		assert online_view['info'] == {'status': 'active'}

		current_time += threshold + 1

		offline_view = client.get(f'/api/agents/{agent_id}', headers={'Authorization': f'Bearer {operator_token}'}).json()
		assert not offline_view['online']
