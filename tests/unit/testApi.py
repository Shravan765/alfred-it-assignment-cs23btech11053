'''Walking-skeleton API and request-log behaviour tests.'''

import logging

from fastapi.testclient import TestClient

from server.app.main import createApp


def testHealthIsPublic(temp_settings):
	'''Serve the health endpoint without authentication.'''
	with TestClient(createApp(temp_settings)) as client:
		assert client.get('/health').status_code == 200


def testOneSafeLogLineForSuccessNotFoundAndFailure(temp_settings, caplog):
	'''Log one record per request without query strings, headers or secrets.'''
	app = createApp(temp_settings)
	@app.get('/raises')
	def raises():
		'''Raise an unhandled exception to exercise the middleware failure path.'''
		raise RuntimeError('private failure')
	with TestClient(app) as client, caplog.at_level(logging.INFO):
		client.get('/health?token=abc')
		client.get('/missing')
		client.get('/raises')
	lines = [record.getMessage() for record in caplog.records if record.name == 'labpc.requests']
	assert len(lines) == 3
	assert any('GET /health 200' in line for line in lines)
	assert any('GET /missing 404' in line for line in lines)
	assert any('GET /raises 500' in line for line in lines)
	assert all('token' not in line and 'abc' not in line and 'Authorization' not in line for line in lines)


def testLoggingSettingsAreRead(temp_settings, caplog):
	'''Honor logging level and actor inclusion from configuration.'''
	from shared.config import loadSettings
	settings = loadSettings(overrides={'paths': {'data_dir': temp_settings.paths.data_dir}, 'logging': {'include_actor': False}})
	with TestClient(createApp(settings)) as client, caplog.at_level(logging.INFO):
		client.get('/health')
	line = next(record.getMessage() for record in caplog.records if record.name == 'labpc.requests')
	assert len(line.split()) == 6
	caplog.clear()
	settings = loadSettings(overrides={'paths': {'data_dir': temp_settings.paths.data_dir}, 'logging': {'level': 'ERROR'}})
	with TestClient(createApp(settings)) as client, caplog.at_level(logging.INFO):
		client.get('/health')
	assert not [record for record in caplog.records if record.name == 'labpc.requests']


def testHealthReturns503WhenDatabaseUnavailable(temp_settings):
	'''Translate database connection failures into an explicit 503 health response.'''
	def unavailable():
		'''Simulate a database path that cannot be opened.'''
		raise OSError('database path unavailable')
	app = createApp(temp_settings)
	with TestClient(app) as client:
		app.state.connection_factory = unavailable
		assert client.get('/health').status_code == 503
