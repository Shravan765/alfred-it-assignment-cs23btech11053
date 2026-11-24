#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
tmp_ca=$(mktemp)
trap 'rm -f "$tmp_ca"' EXIT
docker compose exec -T proxy cat /certs/ca.pem > "$tmp_ca"
curl --fail --silent --show-error --cacert "$tmp_ca" https://localhost:8443/health | grep -q '"status":"ok"'
if curl --silent --output /dev/null --write-out '%{http_code}' http://localhost:8443 | grep -q '^2'; then
	echo 'Plain HTTP unexpectedly succeeded' >&2
	exit 1
fi
if curl --silent --output /dev/null http://localhost:8000/health; then
	echo 'App port 8000 is unexpectedly published' >&2
	exit 1
fi
published=$(docker compose ps --format '{{.Ports}}' | tr ',' '\n' | grep -Eo '[0-9.]*:([0-9]+)->' | sed -E 's/.*:([0-9]+)->.*/\1/' | sort -u || true)
if [ -n "$published" ] && [ "$published" != 8443 ]; then
	echo "Unexpected published host port(s): $published" >&2
	exit 1
fi
