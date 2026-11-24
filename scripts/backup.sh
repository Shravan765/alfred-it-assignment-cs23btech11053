#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
container_file="/tmp/lab-${stamp}.db"
host_file="backups/lab-${stamp}.db"

# Run SQLite's online backup API in the app container, then copy the result out.
docker compose exec -T app python -m app.cli backup "$container_file"
docker compose cp "app:$container_file" "$host_file"
chmod 644 "$host_file"
docker compose exec -T app rm -f "$container_file"
printf 'Backup created: %s\n' "$host_file"
