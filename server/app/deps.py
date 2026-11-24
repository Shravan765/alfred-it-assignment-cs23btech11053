'''Authentication dependencies for user and agent bearer tokens.'''

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.app.security import hashToken

_bearer = HTTPBearer(auto_error=False)


def currentUser(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None):
	'''Resolve a live user session or return HTTP 401.'''
	if credentials is None:
		raise HTTPException(status_code=401, detail='Not authenticated')
	connection = request.app.state.connection_factory()
	try:
		row = connection.execute('SELECT users.id, users.username, users.role FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires_at>? ', (hashToken(credentials.credentials), request.app.state.clock())).fetchone()
	finally:
		connection.close()
	if row is None:
		raise HTTPException(status_code=401, detail='Not authenticated')
	request.state.actor = row['username']
	return row


def currentAgent():
	'''Placeholder dependency reserved for the agent API stage.'''
	raise HTTPException(status_code=401, detail='Not authenticated')
