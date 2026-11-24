'''Admin endpoints for creating, listing and revoking one-time registration codes.'''

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from server.app.clock import toIso
from server.app.db import runInTransaction
from server.app.permissions import requireCapability
from server.app.security import hashToken, newRegistrationCode

router = APIRouter(prefix='/api/admin/registration-codes', tags=['codes'])

ACTIVE_CLAUSE = 'used_at IS NULL AND revoked_at IS NULL AND expires_at > ?'


def codeStatus(row, now: int) -> str:
	'''Derive the public status of a code row; used wins over revoked wins over expired.'''
	if row['used_at'] is not None:
		return 'used'
	if row['revoked_at'] is not None:
		return 'revoked'
	if row['expires_at'] <= now:
		return 'expired'
	return 'active'


def codeView(row, now: int) -> dict[str, Any]:
	'''Describe a code row for the API; the plaintext code is never available.'''
	return {
		'id': row['id'],
		'status': codeStatus(row, now),
		'created_at': toIso(row['created_at']),
		'expires_at': toIso(row['expires_at']),
		'used_at': toIso(row['used_at']),
		'revoked_at': toIso(row['revoked_at']),
		'used_by_agent': row['used_by_agent'],
		'created_by': row['created_by'],
	}


@router.post('', status_code=201)
def createCode(request: Request, user: Annotated[Any, Depends(requireCapability('manage_codes'))]):
	'''Create a code, show its plaintext exactly once and store only its hash.'''
	settings = request.app.state.settings
	now = request.app.state.clock()
	code = newRegistrationCode(settings)
	connection = request.app.state.connection_factory()
	try:
		with runInTransaction(connection):
			active = connection.execute(f'SELECT count(*) FROM registration_codes WHERE {ACTIVE_CLAUSE}', (now,)).fetchone()[0]
			if active >= settings.codes.max_active:
				raise HTTPException(status_code=409, detail='Too many active registration codes')
			expires_at = now + settings.codes.ttl_s
			cursor = connection.execute('INSERT INTO registration_codes(code_hash, created_by, created_at, expires_at) VALUES(?, ?, ?, ?)', (hashToken(code.replace('-', '')), user['id'], now, expires_at))
		return {'id': cursor.lastrowid, 'code': code, 'expires_at': toIso(expires_at)}
	finally:
		connection.close()


@router.get('')
def listCodes(request: Request, user: Annotated[Any, Depends(requireCapability('manage_codes'))]):
	'''List all codes with a derived status; never returns plaintext codes.'''
	now = request.app.state.clock()
	connection = request.app.state.connection_factory()
	try:
		rows = connection.execute('SELECT * FROM registration_codes ORDER BY id DESC').fetchall()
	finally:
		connection.close()
	return {'codes': [codeView(row, now) for row in rows]}


@router.delete('/{code_id}')
def revokeCode(code_id: int, request: Request, user: Annotated[Any, Depends(requireCapability('manage_codes'))]):
	'''Revoke an unused code: 404 if unknown, 409 if already used, idempotent if already revoked.'''
	now = request.app.state.clock()
	connection = request.app.state.connection_factory()
	try:
		with runInTransaction(connection):
			cursor = connection.execute('UPDATE registration_codes SET revoked_at=? WHERE id=? AND used_at IS NULL AND revoked_at IS NULL', (now, code_id))
			if cursor.rowcount == 1:
				return {'status': 'revoked'}
			row = connection.execute('SELECT used_at FROM registration_codes WHERE id=?', (code_id,)).fetchone()
			if row is None:
				raise HTTPException(status_code=404, detail='Registration code not found')
			if row['used_at'] is not None:
				raise HTTPException(status_code=409, detail='Registration code already used')
			return {'status': 'revoked'}
	finally:
		connection.close()
