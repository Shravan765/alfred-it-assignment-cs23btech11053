'''User login, logout and current-session endpoints.'''

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server.app.db import runInTransaction
from server.app.deps import currentUser
from server.app.security import hashToken, newToken, verifyPassword

router = APIRouter(prefix='/api/auth', tags=['auth'])


class LoginBody(BaseModel):
	'''Validated username and password submitted to login.'''
	username: str = Field(min_length=1, max_length=64)
	password: str = Field(min_length=1, max_length=256)


@router.post('/login')
def login(body: LoginBody, request: Request):
	'''Authenticate a user and issue a hashed, expiring bearer session.'''
	connection = request.app.state.connection_factory()
	try:
		user = connection.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE', (body.username,)).fetchone()
		if user is None:
			# Equalise unknown-user work with a real scrypt verification.
			dummy_hash = request.app.state.dummy_password_hash
			verifyPassword(body.password, dummy_hash)
			raise HTTPException(status_code=401, detail='Invalid username or password')
		if not verifyPassword(body.password, user['password_hash']):
			raise HTTPException(status_code=401, detail='Invalid username or password')
		token = newToken(request.app.state.settings)
		now = request.app.state.clock()
		with runInTransaction(connection):
			connection.execute('INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES(?, ?, ?, ?)', (hashToken(token), user['id'], now, now + request.app.state.settings.auth.session_ttl_s))
		request.state.actor = user['username']
		return {'access_token': token, 'token_type': 'bearer', 'user': {'username': user['username'], 'role': user['role']}}
	finally:
		connection.close()


@router.post('/logout')
def logout(request: Request, user: Annotated[Any, Depends(currentUser)]):
	'''Delete the current session token hash.'''
	credentials = request.headers.get('authorization', '').split(' ', 1)[-1]
	connection = request.app.state.connection_factory()
	try:
		with runInTransaction(connection):
			connection.execute('DELETE FROM sessions WHERE token_hash=?', (hashToken(credentials),))
	finally:
		connection.close()
	return {'status': 'ok'}


@router.get('/me')
def me(user: Annotated[Any, Depends(currentUser)]):
	'''Return the authenticated user's public identity.'''
	return {'id': user['id'], 'username': user['username'], 'role': user['role']}
