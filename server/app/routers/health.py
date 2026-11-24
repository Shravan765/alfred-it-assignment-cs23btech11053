'''Unauthenticated service health endpoint.'''

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get('/health')
def health(request: Request):
	'''Report process health and, after Stage 2, database reachability.'''
	try:
		connection = request.app.state.connection_factory()
		try:
			connection.execute('SELECT 1')
		finally:
			connection.close()
	except Exception as error:
		raise HTTPException(status_code=503, detail='Database unavailable') from error
	return {'status': 'ok'}
