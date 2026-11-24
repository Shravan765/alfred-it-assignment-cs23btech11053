'''UTC epoch clock with a replaceable function for deterministic tests.'''

import time

clock_function = time.time


def nowEpoch() -> int:
	'''Return the current UTC time as integer epoch seconds.'''
	return int(clock_function())


def setClock(clock) -> None:
	'''Replace the clock function for tests.'''
	global clock_function
	clock_function = clock
