'''FastAPI application factory and lifecycle wiring.'''

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.app.clock import nowEpoch
from server.app.db import getConnection, initSchema
from server.app.routers import agentApi, agents, auth, codes, health
from server.app.security import hashPassword
from server.app.seed import seedUsers
from shared.config import Settings, loadSettings


def createApp(settings: Settings | None = None, connection_factory=None, clock=None) -> FastAPI:
	'''Build the API with injectable settings, database factory and clock.'''
	resolved_settings = settings or loadSettings()
	logging.basicConfig(level=getattr(logging, resolved_settings.logging.level), format='%(message)s')
	logger = logging.getLogger('labpc.requests')
	logger.setLevel(getattr(logging, resolved_settings.logging.level))
	factory = connection_factory or (lambda: getConnection(resolved_settings))
	current_clock = clock or nowEpoch

	@asynccontextmanager
	async def lifespan(app: FastAPI):
		'''Initialise persistent schema and sample users on application startup.'''
		connection = factory()
		try:
			initSchema(connection)
			seedUsers(resolved_settings, connection, current_clock())
		finally:
			connection.close()
		yield

	app = FastAPI(title='Lab PC Agent', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
	app.state.settings = resolved_settings
	app.state.connection_factory = factory
	app.state.clock = current_clock
	app.state.dummy_password_hash = hashPassword('not-a-real-password', resolved_settings)
	app.include_router(health.router)
	app.include_router(auth.router)
	app.include_router(codes.router)
	app.include_router(agents.router)
	app.include_router(agentApi.router)

	@app.middleware('http')
	async def requestLogging(request: Request, call_next):
		'''Emit one safe request log record, including handled server failures.'''
		started = time.perf_counter()
		status = 500
		try:
			response = await call_next(request)
			status = response.status_code
			return response
		except Exception:  # noqa: BLE001
			return JSONResponse(status_code=500, content={'detail': 'Internal server error'})
		finally:
			elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
			if logger.isEnabledFor(logging.INFO):
				parts = [str(current_clock()), request.method, request.url.path, str(status), str(elapsed_ms), request.client.host if request.client else '-']
				if resolved_settings.logging.include_actor:
					parts.append(getattr(request.state, 'actor', '-'))
				logger.info(' '.join(parts))

	return app
