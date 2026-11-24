#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

# Runs inside the app container against the internal listener (the HTTPS proxy path
# is already covered by e2e_stage1.sh). Needs a running Compose stack.
docker compose exec -T app python - <<'PY'
import json
import sqlite3
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8000'


def call(method, path, body=None, token=None):
	headers = {'Content-Type': 'application/json'}
	if token:
		headers['Authorization'] = 'Bearer ' + token
	data = json.dumps(body).encode() if body is not None else None
	request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
	try:
		with urllib.request.urlopen(request, timeout=5) as response:
			return response.status, json.loads(response.read() or b'{}')
	except urllib.error.HTTPError as error:
		return error.code, json.loads(error.read() or b'{}')


def expect(label, actual, wanted):
	if actual != wanted:
		raise SystemExit(f'FAIL {label}: got {actual}, wanted {wanted}')
	print(f'ok   {label}')


credentials = dict(line.strip().split(':', 1) for line in open('/data/sample_credentials.txt'))
tokens = {}
for user in ('admin', 'operator'):
	status, body = call('POST', '/api/auth/login', {'username': user, 'password': credentials[user]})
	expect(f'{user} login', status, 200)
	tokens[user] = body['access_token']

status, code_data = call('POST', '/api/admin/registration-codes', token=tokens['admin'])
expect('admin creates registration code', status, 201)

name = f'e2e-stage6-{int(time.time())}'
status, agent = call('POST', '/api/agent/register', {'code': code_data['code'], 'name': name})
expect('register agent', status, 201)
agent_id = agent['agent_id']
agent_token = agent['token']

status, pre_view = call('GET', f'/api/agents/{agent_id}', token=tokens['operator'])
expect('operator sees new agent', status, 200)
expect('new agent starts offline', pre_view['online'], False)
expect('new agent has no last_seen_at', pre_view['last_seen_at'], None)

info_payload = {'os': 'linux', 'arch': 'x86_64', 'e2e': True}
status, hb = call('POST', '/api/agent/heartbeat', {'info': info_payload}, token=agent_token)
expect('heartbeat accepted', status, 200)
expect('heartbeat returns interval', hb['interval_s'], 30)
expect('heartbeat returns empty actions', hb['actions'], [])

status, post_view = call('GET', f'/api/agents/{agent_id}', token=tokens['operator'])
expect('operator sees agent online', post_view['online'], True)
expect('operator sees updated info', post_view['info'], info_payload)
expect('operator sees last_seen_at set', post_view['last_seen_at'] is not None, True)

status, listing = call('GET', '/api/agents', token=tokens['operator'])
expect('operator lists agents', status, 200)
our_agent = [a for a in listing['agents'] if a['id'] == agent_id][0]
expect('listed agent is online', our_agent['online'], True)

# Remove the throw-away agent so the database returns to clean state
connection = sqlite3.connect('/data/lab.db')
connection.execute('DELETE FROM agents WHERE id=?', (agent_id,))
connection.commit()
connection.close()

print('Stage 6 heartbeat and online status checks passed.')
PY
