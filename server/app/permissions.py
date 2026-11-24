'''Single role-to-capability policy table.'''

from typing import Annotated, Any

from fastapi import Depends, HTTPException

from server.app.deps import currentUser

CAPABILITIES = {
	'view_agents': frozenset({'admin', 'operator'}),
	'manage_users': frozenset({'admin'}),
	'manage_codes': frozenset({'admin'}),
	'manage_agents': frozenset({'admin'}),
	'manage_actions': frozenset({'admin', 'operator'}),
}


def requireCapability(capability: str):
	'''Build a FastAPI dependency that enforces one named capability.'''
	def checkCapability(user: Annotated[Any, Depends(currentUser)]):
		'''Reject users whose role is not in the capability policy.'''
		if user['role'] not in CAPABILITIES.get(capability, frozenset()):
			raise HTTPException(status_code=403, detail='Insufficient permissions')
		return user
	return checkCapability
