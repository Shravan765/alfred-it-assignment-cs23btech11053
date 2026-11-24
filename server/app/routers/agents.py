'''Operator and admin endpoints for viewing registered agents and their online status.'''

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from server.app.clock import toIso
from server.app.permissions import requireCapability

router = APIRouter(prefix='/api/agents', tags=['agents'])


def isOnline(last_seen_at: int | None, now: int, threshold_s: int) -> bool:
	'''Return whether an agent is considered online based on its last heartbeat.'''
	if last_seen_at is None:
		return False
	return (now - last_seen_at) <= threshold_s


def agentView(row, now: int, settings) -> dict[str, Any]:
	'''Describe an agent row for the public API.'''
	info = {}
	if row['info_json']:
		try:
			info = json.loads(row['info_json'])
		except (json.JSONDecodeError, TypeError):
			info = {}
	return {
		'id': row['id'],
		'name': row['name'],
		'registered_at': toIso(row['registered_at']),
		'last_seen_at': toIso(row['last_seen_at']),
		'last_ip': row['last_ip'],
		'info': info,
		'online': isOnline(row['last_seen_at'], now, settings.agents.online_threshold_s),
	}


@router.get('')
def listAgents(request: Request, user: Annotated[Any, Depends(requireCapability('view_agents'))]):
	'''List all registered agents with their online status.'''
	now = request.app.state.clock()
	settings = request.app.state.settings
	connection = request.app.state.connection_factory()
	try:
		rows = connection.execute('SELECT * FROM agents ORDER BY id ASC').fetchall()
	finally:
		connection.close()
	return {'agents': [agentView(row, now, settings) for row in rows]}


@router.get('/{agent_id}')
def getAgent(agent_id: int, request: Request, user: Annotated[Any, Depends(requireCapability('view_agents'))]):
	'''Get single agent details including online status and machine info.'''
	now = request.app.state.clock()
	settings = request.app.state.settings
	connection = request.app.state.connection_factory()
	try:
		row = connection.execute('SELECT * FROM agents WHERE id=?', (agent_id,)).fetchone()
	finally:
		connection.close()
	if row is None:
		raise HTTPException(status_code=404, detail='Agent not found')
	return agentView(row, now, settings)
