'''The fixed action names understood by both server and agent.'''

ACTION_KINDS = frozenset({
	'clear_print_queue',
	'restart_print_spooler',
	'flush_dns_cache',
	'get_disk_usage',
})
