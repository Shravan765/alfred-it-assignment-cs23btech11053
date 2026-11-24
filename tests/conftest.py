'''Shared pytest fixtures for configuration, application and deterministic time.'''

from pathlib import Path

import pytest

from shared.config import loadSettings


@pytest.fixture
def settings():
	'''Load shipped settings with the inexpensive unit-test overlay.'''
	return loadSettings(overlay_path=Path(__file__).with_name('config_overlay.unit.toml'))


@pytest.fixture(name='fake_clock')
def fakeClock():
	'''Return a mutable fake epoch clock for boundary tests.'''
	class Clock:
		'''Small callable clock whose value tests can advance.'''
		def __init__(self):
			'''Initialise the fake clock at a stable epoch.'''
			self.value = 1_800_000_000

		def __call__(self):
			'''Return the current fake epoch.'''
			return self.value

		def advance(self, seconds):
			'''Advance fake time by a specified number of seconds.'''
			self.value += seconds
	return Clock()


@pytest.fixture(name='temp_settings')
def tempSettings(settings, tmp_path):
	'''Return settings with isolated persistent paths for one test.'''
	return loadSettings(overrides={'paths': {'data_dir': str(tmp_path)}})
