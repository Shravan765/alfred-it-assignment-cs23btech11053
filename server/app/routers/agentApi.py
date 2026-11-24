'''Agent-facing endpoints: registration with a one-time code.'''

import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from server.app.db import runInTransaction
from server.app.security import hashToken, newToken, normaliseCode

router = APIRouter(prefix='/api/agent', tags=['agent'])

NAME_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
REDEEM_SQL = 'UPDATE registration_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL AND revoked_at IS NULL AND expires_at > ?'


class RegisterBody(BaseModel):
	'''Registration code and requested agent name.'''
	code: str = Field(min_length=1, max_length=256)
	name: str = Field(min_length=1, max_length=256)


def invalidCode() -> HTTPException:
	'''Build the single generic error used for unknown, used, expired and revoked codes.'''
	return HTTPException(status_code=403, detail='Invalid registration code')


@router.post('/register', status_code=201)
def registerAgent(body: RegisterBody, request: Request):
	'''Redeem a code atomically and create the agent, returning its token once.'''
	settings = request.app.state.settings
	if len(body.name) > settings.agents.name_max_length or not NAME_PATTERN.fullmatch(body.name):
		raise HTTPException(status_code=422, detail='Invalid agent name')
	canonical = normaliseCode(body.code, settings)
	if canonical is None:
		raise invalidCode()
	now = request.app.state.clock()
	token = newToken(settings)
	code_hash = hashToken(canonical)
	connection = request.app.state.connection_factory()
	try:
		# One transaction: redeem first, so a duplicate name rolls the code back unused
		# and name existence is never revealed to someone without a valid code.
		with runInTransaction(connection):
			if connection.execute(REDEEM_SQL, (now, code_hash, now)).rowcount != 1:
				raise invalidCode()
			try:
				cursor = connection.execute('INSERT INTO agents(name, token_hash, registered_at) VALUES(?, ?, ?)', (body.name, hashToken(token), now))
			except sqlite3.IntegrityError as error:
				raise HTTPException(status_code=409, detail='Agent name already registered') from error
			agent_id = cursor.lastrowid
			connection.execute('UPDATE registration_codes SET used_by_agent=? WHERE code_hash=?', (agent_id, code_hash))
	finally:
		connection.close()
	request.state.actor = body.name
	return {'agent_id': agent_id, 'token': token}
