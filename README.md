# Lab PC Agent

A staged Python service for managing lab PCs. The implementation currently covers Stages 0–4 of `BUILD_PLAN.md`:

- Validated TOML configuration and repository style guardrails.
- FastAPI service behind a local HTTPS reverse proxy.
- SQLite persistence using WAL, foreign keys and explicit transactions.
- Seeded admin/operator accounts, scrypt password hashing and expiring bearer sessions.
- Consistent online SQLite backups and validated, atomic database restores.

## Requirements

- Conda or Mamba with conda-forge access for the `labpc` environment.
- Docker Engine and the Docker Compose plugin for the container workflow.
- `curl` and a POSIX-compatible shell for the full integration gate.

## Set up the development environment

```sh
conda env create -f environment.yml
conda activate labpc
```

The environment includes Python, FastAPI, Uvicorn, HTTPX, pytest, pytest-timeout, Ruff and Pyright. To recreate it after dependency changes:

```sh
conda env update -f environment.yml --prune
```

## Run the service

Build and start the Compose stack from the repository root:

```sh
docker compose up --build -d
```

Only the HTTPS proxy is published on host port `8443`; the API listens on port `8000` inside the Compose network. Retrieve the local CA certificate and check health:

```sh
tmp_ca=$(mktemp)
docker compose exec -T proxy cat /certs/ca.pem > "$tmp_ca"
curl --cacert "$tmp_ca" https://localhost:8443/health
rm -f "$tmp_ca"
```

On first start, random sample passwords are written once to `/data/sample_credentials.txt` with mode `600`. Read them with:

```sh
docker compose exec app cat /data/sample_credentials.txt
```

The seeded usernames are `admin` and `operator`. Passwords are stored in SQLite only as salted scrypt hashes. The database and credentials file live in the persistent `app_data` volume. `docker compose down` preserves named volumes; `docker compose down -v` deletes them.

## Run tests and static checks

Activate the `labpc` Conda environment, then run the quick gate:

```sh
./scripts/test.sh quick
```

It runs, in order:

1. `ruff check .` (lint only; do not run `ruff format`, because the assignment requires triple-single-quoted docstrings and tab indentation).
2. `pyright` using `pyrightconfig.json`.
3. `python -m pytest -q`, including the repository's custom source-style checker.

The same checks can be run individually:

```sh
ruff check .
pyright
python -m pytest -q
```

For a clean Docker-backed integration gate, run:

```sh
./scripts/test.sh
```

**Warning:** the full gate starts with `docker compose down -v`, which deletes the Compose database and certificate volumes. Do not run it against data you need to preserve. It then builds the stack, checks HTTPS and persistence, exercises backup/restore, and runs lint, type checking and unit tests in the tests image. The `scripts/e2e_stage*.sh` scripts require a running Compose stack.

## Backup and restore

Create a timestamped backup under `./backups/`:

```sh
./scripts/backup.sh
```

The script invokes `python -m app.cli backup` inside the running app container. The CLI uses SQLite's online backup API, so the backup is transactionally consistent while the application is accepting writes. The resulting host file is mode `644` and the `backups/` directory is git-ignored.

Restore a backup by its filename (not a path) from `./backups/`:

```sh
./scripts/restore.sh lab-YYYYMMDDTHHMMSSZ.db
```

The restore script stops the app, mounts the backup directory read-only into a one-off app container, validates `PRAGMA integrity_check` and `meta.schema_version`, copies the database to a temporary file in the data volume, and atomically replaces the live database. It removes stale SQLite `-wal` and `-shm` sidecars, starts the Compose services again, and waits for the app health endpoint. Invalid backups are rejected before the live database is changed.

**Important:** restore replaces the database only. The sample credentials file is not included in a backup. Restoring a database from a different installation may therefore leave `/data/sample_credentials.txt` stale or inconsistent with the restored user password hashes. Keep credentials separately and reconcile them if restoring across installations.

The lower-level CLI commands are also available inside the app container:

```sh
docker compose exec app python -m app.cli backup /tmp/lab-backup.db
docker compose cp app:/tmp/lab-backup.db ./lab-backup.db
docker compose exec app python -m app.cli restore /tmp/lab-backup.db
```

Run restore only while the application is stopped; the provided `scripts/restore.sh` handles this lifecycle safely.

## Configuration

`config_settings.toml` is the single source of defaults. `LAB_CONFIG_FILE` selects the base file and optional `LAB_CONFIG_OVERLAY` selects a partial TOML overlay. Tests may use nested `overrides`; overlays and overrides cannot introduce unknown keys. `pyrightconfig.json` defines the static type-checking scope and mode; `ruff.toml` sets the project lint line length.

## Current scope and known limitations

Agent registration, heartbeat, agent runtime, action execution, rate limiting and the browser UI are later stages. The agent Docker target remains a placeholder until its planned stage. Conda dependency solving and Docker-based integration checks must be run in an environment with Conda/Mamba and Docker installed.
