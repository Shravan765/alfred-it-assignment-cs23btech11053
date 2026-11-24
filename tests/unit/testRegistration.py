'''Registration-code and agent-registration tests (Stage 5).'''

import threading
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from server.app.deps import currentAgent
from server.app.main import createApp
from server.app.security import CODE_ALPHABET, hashToken, newRegistrationCode, normaliseCode
from shared.config import loadSettings

OVERLAY = Path(__file__).resolve().parents[1] / 'config_overlay.unit.toml'
CODES_URL = '/api/admin/registration-codes'


def makeSettings(tmp_path, **sections):
	'''Load unit-test settings with an isolated data dir and optional section overrides.'''
	overrides = {'paths': {'data_dir': str(tmp_path)}}
	overrides.update(sections)
	return loadSettings(overlay_path=OVERLAY, overrides=overrides)


def loginHeaders(client, settings, username):
	'''Log in as a seeded user and return its bearer header.'''
	path = Path(settings.paths.data_dir) / settings.paths.credentials_file
	credentials = dict(line.split(':', 1) for line in path.read_text().splitlines())
	response = client.post('/api/auth/login', json={'username': username, 'password': credentials[username]})
	return {'Authorization': f"Bearer {response.json()['access_token']}"}


def newCode(client, headers):
	'''Create a code as admin and return the plaintext.'''
	response = client.post(CODES_URL, headers=headers)
	assert response.status_code == 201
	return response.json()['code']


def register(client, code, name):
	'''Call the registration endpoint.'''
	return client.post('/api/agent/register', json={'code': code, 'name': name})


def testCodeAlphabetIsCrockfordBase32():
	'''The alphabet has 32 unique characters and none of the look-alikes I, L, O, U.'''
	assert len(CODE_ALPHABET) == len(set(CODE_ALPHABET)) == 32
	assert not set('ILOU') & set(CODE_ALPHABET)


def testGeneratedCodesMatchSettingsAndNeverRepeat(settings):
	'''Codes follow the configured length and grouping, and 2000 samples are all distinct.'''
	codes = [newRegistrationCode(settings) for _ in range(2000)]
	assert len(set(codes)) == 2000
	for code in codes[:50]:
		groups = code.split('-')
		assert all(len(group) == settings.codes.group_size for group in groups)
		assert len(''.join(groups)) == settings.codes.length
		assert set(code.replace('-', '')) <= set(CODE_ALPHABET)


def testNormaliseCodeIgnoresCaseAndDashes(settings):
	'''Case, dashes and spaces are ignored; wrong length or characters are rejected.'''
	code = newRegistrationCode(settings)
	canonical = code.replace('-', '')
	assert normaliseCode(code.lower(), settings) == canonical
	assert normaliseCode(canonical, settings) == canonical
	assert normaliseCode(code.replace('-', ' '), settings) == canonical
	assert normaliseCode(canonical[:-1], settings) is None
	assert normaliseCode(canonical + 'A', settings) is None
	assert normaliseCode('U' * settings.codes.length, settings) is None


def testAdminCreatesCodeOperatorForbidden(tmp_path):
	'''Admin gets a one-time plaintext code with expiry from config; operator gets 403.'''
	settings = makeSettings(tmp_path)
	with TestClient(createApp(settings)) as client:
		admin = loginHeaders(client, settings, 'admin')
		operator = loginHeaders(client, settings, 'operator')
		assert client.post(CODES_URL, headers=operator).status_code == 403
		assert client.post(CODES_URL).status_code == 401
		response = client.post(CODES_URL, headers=admin)
		assert response.status_code == 201
		body = response.json()
		assert len(body['code'].replace('-', '')) == settings.codes.length
		assert body['code'].count('-') == settings.codes.length // settings.codes.group_size - 1


def testRegisterSucceedsOnceThenCodeIsSpent(tmp_path):
	'''A valid code registers an agent once; a second use is rejected.'''
	settings = makeSettings(tmp_path)
	with TestClient(createApp(settings)) as client:
		admin = loginHeaders(client, settings, 'admin')
		code = newCode(client, admin)
		first = register(client, code.lower(), 'pc-01')
		assert first.status_code == 201
		assert set(first.json()) == {'agent_id', 'token'}
		assert register(client, code, 'pc-02').status_code == 403


@pytest.mark.timeout(30)
def testConcurrentRedeemHasExactlyOneWinner(tmp_path):
	'''Five threads redeem the same code: exactly one gets 201, the rest get 4xx.'''
	settings = makeSettings(tmp_path)
	app = createApp(settings)
	with TestClient(app) as client:
		code = newCode(client, loginHeaders(client, settings, 'admin'))
	statuses = []
	gate = threading.Barrier(5)

	def redeem(index):
		'''Wait for all threads, then try to register under a distinct name.'''
		gate.wait()
		statuses.append(register(TestClient(app), code, f'racer-{index}').status_code)
	threads = [threading.Thread(target=redeem, args=(index,)) for index in range(5)]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join()
	assert sorted(statuses).count(201) == 1
	assert len(statuses) == 5
	assert all(400 <= status < 500 for status in statuses if status != 201)


def testExpiryBoundary(tmp_path, fake_clock):
	'''A code works at ttl_s - 1 seconds and is rejected from ttl_s seconds on.'''
	settings = makeSettings(tmp_path)
	with TestClient(createApp(settings, clock=fake_clock)) as client:
		admin = loginHeaders(client, settings, 'admin')
		early = newCode(client, admin)
		late = newCode(client, admin)
		fake_clock.advance(settings.codes.ttl_s - 1)
		assert register(client, early, 'pc-early').status_code == 201
		fake_clock.advance(1)
		assert register(client, late, 'pc-late').status_code == 403
		fake_clock.advance(1)
		assert register(client, late, 'pc-late').status_code == 403


def testUnknownUsedExpiredRevokedGiveSameError(tmp_path, fake_clock):
	'''Unknown, malformed, used, expired and revoked codes are indistinguishable.'''
	settings = makeSettings(tmp_path)
	with TestClient(createApp(settings, clock=fake_clock)) as client:
		admin = loginHeaders(client, settings, 'admin')
		used = newCode(client, admin)
		assert register(client, used, 'pc-used').status_code == 201
		revoked_response = client.post(CODES_URL, headers=admin).json()
		assert client.delete(f"{CODES_URL}/{revoked_response['id']}", headers=admin).status_code == 200
		expired = newCode(client, admin)
		fake_clock.advance(settings.codes.ttl_s)
		attempts = [
			register(client, newRegistrationCode(settings), 'pc-x'),
			register(client, 'not a code', 'pc-x'),
			register(client, used, 'pc-x'),
			register(client, revoked_response['code'], 'pc-x'),
			register(client, expired, 'pc-x'),
		]
		assert {response.status_code for response in attempts} == {403}
		assert len({response.text for response in attempts}) == 1


def testSecretsAreNeverStoredInPlaintext(tmp_path):
	'''Neither the code nor the token appears in the database files; their hashes do.'''
	settings = makeSettings(tmp_path)
	with TestClient(createApp(settings)) as client:
		admin = loginHeaders(client, settings, 'admin')
		code = newCode(client, admin)
		token = register(client, code, 'pc-01').json()['token']
		blob = b''.join(path.read_bytes() for path in tmp_path.glob(f'{settings.paths.database_file}*'))
		assert hashToken(token).encode() in blob
		assert hashToken(code.replace('-', '')).encode() in blob
		assert token.encode() not in blob
		assert code.encode() not in blob
		assert code.replace('-', '').encode() not in blob


def testListShowsStatusButNeverPlaintext(tmp_path):
	'''Listing reports active/used/revoked status and exposes no plaintext or token.'''
	settings = makeSettings(tmp_path)
	with TestClient(createApp(settings)) as client:
		admin = loginHeaders(client, settings, 'admin')
		operator = loginHeaders(client, settings, 'operator')
		used = newCode(client, admin)
		token = register(client, used, 'pc-01').json()['token']
		newCode(client, admin)
		gone = client.post(CODES_URL, headers=admin).json()
		client.delete(f"{CODES_URL}/{gone['id']}", headers=admin)
		listing = client.get(CODES_URL, headers=admin)
		assert [item['status'] for item in listing.json()['codes']] == ['revoked', 'active', 'used']
		assert used not in listing.text and used.replace('-', '') not in listing.text
		assert token not in listing.text and gone['code'] not in listing.text
		assert client.get(CODES_URL, headers=operator).status_code == 403


def testMaxActiveIsEnforced(tmp_path):
	'''Creating beyond codes.max_active gives 409; revoking or using a code frees a slot.'''
	settings = makeSettings(tmp_path, codes={'max_active': 2})
	with TestClient(createApp(settings)) as client:
		admin = loginHeaders(client, settings, 'admin')
		first = client.post(CODES_URL, headers=admin).json()
		second = newCode(client, admin)
		assert client.post(CODES_URL, headers=admin).status_code == 409
		client.delete(f"{CODES_URL}/{first['id']}", headers=admin)
		assert client.post(CODES_URL, headers=admin).status_code == 201
		assert client.post(CODES_URL, headers=admin).status_code == 409
		assert register(client, second, 'pc-01').status_code == 201
		assert client.post(CODES_URL, headers=admin).status_code == 201


def testDuplicateNameIsConflictAndKeepsCodeUsable(tmp_path):
	'''A taken name (any case) gives 409 and does not burn the code.'''
	settings = makeSettings(tmp_path)
	with TestClient(createApp(settings)) as client:
		admin = loginHeaders(client, settings, 'admin')
		assert register(client, newCode(client, admin), 'pc-01').status_code == 201
		spare = newCode(client, admin)
		assert register(client, spare, 'PC-01').status_code == 409
		assert register(client, spare, 'pc-02').status_code == 201


def testInvalidNamesAreRejectedWithoutSpendingCode(tmp_path):
	'''Over-long names and bad characters give 422 and leave the code unused.'''
	settings = makeSettings(tmp_path)
	bad_names = ['a' * (settings.agents.name_max_length + 1), 'has space', '.hidden', '-dash', 'semi;colon', "quote'"]
	with TestClient(createApp(settings)) as client:
		code = newCode(client, loginHeaders(client, settings, 'admin'))
		for name in bad_names:
			assert register(client, code, name).status_code == 422, name
		assert register(client, code, 'a' * settings.agents.name_max_length).status_code == 201


def testRegisterBodyValidation(tmp_path):
	'''Missing fields give 422.'''
	settings = makeSettings(tmp_path)
	with TestClient(createApp(settings)) as client:
		assert client.post('/api/agent/register', json={}).status_code == 422
		assert client.post('/api/agent/register', json={'code': 'x'}).status_code == 422


def testRevokeRules(tmp_path):
	'''Revoke: 404 for unknown id, 409 for a used code, idempotent otherwise, and blocks redeeming.'''
	settings = makeSettings(tmp_path)
	with TestClient(createApp(settings)) as client:
		admin = loginHeaders(client, settings, 'admin')
		assert client.delete(f'{CODES_URL}/999999', headers=admin).status_code == 404
		fresh = client.post(CODES_URL, headers=admin).json()
		assert client.delete(f"{CODES_URL}/{fresh['id']}", headers=admin).status_code == 200
		assert client.delete(f"{CODES_URL}/{fresh['id']}", headers=admin).status_code == 200
		assert register(client, fresh['code'], 'pc-01').status_code == 403
		spent = client.post(CODES_URL, headers=admin).json()
		assert register(client, spent['code'], 'pc-02').status_code == 201
		assert client.delete(f"{CODES_URL}/{spent['id']}", headers=admin).status_code == 409


def testTokensDoNotCrossPrincipals(tmp_path):
	'''A user token fails on agent endpoints and an agent token fails on user endpoints.'''
	settings = makeSettings(tmp_path)
	app = createApp(settings)

	@app.get('/api/probe/agent')
	def agentProbe(agent: Annotated[Any, Depends(currentAgent)]):
		'''Return the authenticated agent name to prove the agent token works.'''
		return {'name': agent['name']}
	with TestClient(app) as client:
		admin = loginHeaders(client, settings, 'admin')
		token = register(client, newCode(client, admin), 'pc-01').json()['token']
		agent = {'Authorization': f'Bearer {token}'}
		assert client.get('/api/probe/agent', headers=agent).json() == {'name': 'pc-01'}
		assert client.get('/api/probe/agent', headers=admin).status_code == 401
		assert client.get('/api/probe/agent').status_code == 401
		assert client.get('/api/auth/me', headers=agent).status_code == 401
		assert client.get(CODES_URL, headers=agent).status_code == 401
		assert client.post(CODES_URL, headers=agent).status_code == 401
