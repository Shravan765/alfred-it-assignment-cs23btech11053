#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
read_boot_count() {
	docker compose exec -T app python -c "import sqlite3; connection=sqlite3.connect('/data/lab.db'); print(connection.execute(\"SELECT value FROM meta WHERE key='boot_count'\").fetchone()[0])"
}
first_count=$(read_boot_count)
if [ "$first_count" -ne 1 ]; then
	echo "Expected fresh boot_count 1, got $first_count" >&2
	exit 1
fi
credentials_before=$(docker compose exec -T app sha256sum /data/sample_credentials.txt | awk '{print $1}')
docker compose down
docker compose up -d
attempt=0
until second_count=$(read_boot_count 2>/dev/null); do
	attempt=$((attempt + 1))
	if [ "$attempt" -ge 120 ]; then
		docker compose logs
		exit 1
	fi
	sleep 1
done
attempt=0
until docker compose exec -T app test -s /data/sample_credentials.txt; do
	attempt=$((attempt + 1))
	if [ "$attempt" -ge 120 ]; then
		docker compose logs
		exit 1
	fi
	sleep 1
done
credentials_after=$(docker compose exec -T app sha256sum /data/sample_credentials.txt | awk '{print $1}')
login_status=$(docker compose exec -T app python -c 'import json,urllib.request; credentials=dict(line.strip().split(":",1) for line in open("/data/sample_credentials.txt")); body=json.dumps({"username":"admin","password":credentials["admin"]}).encode(); request=urllib.request.Request("http://127.0.0.1:8000/api/auth/login", data=body, headers={"Content-Type":"application/json"}); print(urllib.request.urlopen(request).status)')
if [ "$second_count" -ne "$((first_count + 1))" ]; then
	echo "boot_count did not increment: $first_count -> $second_count" >&2
	exit 1
fi
if [ "$login_status" != 200 ]; then
	echo 'Sample admin credentials failed after down/up' >&2
	exit 1
fi
if [ "$credentials_before" != "$credentials_after" ]; then
	echo 'Sample credentials changed across down/up' >&2
	exit 1
fi
echo 'Stage 2/3 persistence and seeded-login checks passed.'
