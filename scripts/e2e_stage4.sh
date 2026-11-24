#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

marker="backup-marker-$(date -u +%s)"
docker compose exec -T app python -c "import sqlite3; c=sqlite3.connect('/data/lab.db'); c.execute(\"INSERT INTO users(username,password_hash,role,created_at) VALUES(?, 'hash', 'operator', 0)\", ('$marker',)); c.commit(); c.close()"
./scripts/backup.sh
backup_file=$(ls -1t backups/lab-*.db | head -n 1)
backup_name=${backup_file#backups/}
docker compose exec -T app python -c "import sqlite3; c=sqlite3.connect('/data/lab.db'); c.execute('DELETE FROM users WHERE username=?', ('$marker',)); c.commit(); c.close()"
./scripts/restore.sh "$backup_name"
restored=$(docker compose exec -T app python -c "import sqlite3; c=sqlite3.connect('/data/lab.db'); print(c.execute('SELECT count(*) FROM users WHERE username=?', ('$marker',)).fetchone()[0]); c.close()")
if [ "$restored" != 1 ]; then
	echo 'Restore did not recover the backed-up marker' >&2
	exit 1
fi
docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
echo 'Stage 4 backup/restore round-trip passed.'
