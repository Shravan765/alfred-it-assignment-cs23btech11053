'''UTC epoch clock with a replaceable function for deterministic tests.'''

import time
from datetime import UTC, datetime

clock_function = time.time


def nowEpoch() -> int:
	'''Return the current UTC time as integer epoch seconds.'''
	return int(clock_function())


def setClock(clock) -> None:
	'''Replace the clock function for tests.'''
	global clock_function
	clock_function = clock


def toIso(epoch: int | None) -> str | None:
	'''Format epoch seconds as an ISO-8601 UTC string, passing None through.'''
	if epoch is None:
		return None
	return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace('+00:00', 'Z')
