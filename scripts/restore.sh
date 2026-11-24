#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

if [ "$#" -ne 1 ]; then
	echo "Usage: $0 <backup-filename>" >&2
	exit 2
fi
backup_file=$1
case "$backup_file" in
	*/*|*\\*) echo 'Pass a filename located directly inside ./backups' >&2; exit 2 ;;
esac
if [ ! -f "backups/$backup_file" ]; then
	echo "Backup not found: backups/$backup_file" >&2
	exit 1
fi

# Stop the writer before replacing the database file. Always attempt to bring
# the app back, even when validation or restore fails.
docker compose stop app
restart_app() {
	docker compose up -d
}
trap restart_app EXIT

docker compose run --rm --no-deps -v "$(pwd)/backups:/backups:ro" app \
	python -m app.cli restore "/backups/$backup_file"
restart_app
trap - EXIT

attempt=0
until docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" >/dev/null 2>&1; do
	attempt=$((attempt + 1))
	if [ "$attempt" -ge 120 ]; then
		docker compose logs app
		exit 1
	fi
	sleep 1
done
printf 'Restored database from backups/%s\n' "$backup_file"
