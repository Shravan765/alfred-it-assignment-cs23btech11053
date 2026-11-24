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

codes = '/api/admin/registration-codes'
expect('operator cannot create a code', call('POST', codes, token=tokens['operator'])[0], 403)
expect('anonymous cannot create a code', call('POST', codes)[0], 401)
status, created = call('POST', codes, token=tokens['admin'])
expect('admin creates a code', status, 201)

name = f'e2e-{int(time.time())}'
status, agent = call('POST', '/api/agent/register', {'code': created['code'].lower(), 'name': name})
expect('register with the code', status, 201)
expect('code cannot be reused', call('POST', '/api/agent/register', {'code': created['code'], 'name': name + 'b'})[0], 403)
expect('unknown code is rejected', call('POST', '/api/agent/register', {'code': 'AAAA-AAAA-AAAA', 'name': name + 'c'})[0], 403)

status, listing = call('GET', codes, token=tokens['admin'])
expect('listing works', status, 200)
mine = [item for item in listing['codes'] if item['id'] == created['id']][0]
expect('listing shows the code as used', mine['status'], 'used')
expect('listing hides plaintext', created['code'] in json.dumps(listing), False)
expect('revoking a used code is a conflict', call('DELETE', f"{codes}/{created['id']}", token=tokens['admin'])[0], 409)

status, spare = call('POST', codes, token=tokens['admin'])
expect('second code created', status, 201)
expect('revoke an unused code', call('DELETE', f"{codes}/{spare['id']}", token=tokens['admin'])[0], 200)
expect('revoked code is rejected', call('POST', '/api/agent/register', {'code': spare['code'], 'name': name + 'd'})[0], 403)

# Remove the throw-away agent so the demo fleet stays exactly as seeded.
connection = sqlite3.connect('/data/lab.db')
connection.execute('DELETE FROM agents WHERE name=?', (name,))
connection.commit()
connection.close()
print('Stage 5 registration checks passed.')
PY
