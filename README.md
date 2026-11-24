# Lab PC Agent

A staged Python service for managing lab PCs. The implementation currently covers Stages 0–6 of `BUILD_PLAN.md`:

- Validated TOML configuration and repository style guardrails.
- FastAPI service behind a local HTTPS reverse proxy.
- SQLite persistence using WAL, foreign keys and explicit transactions.
- Seeded admin/operator accounts, scrypt password hashing and expiring bearer sessions.
- Consistent online SQLite backups and validated, atomic database restores.
- Admin-created one-time registration codes and agent registration (`POST /api/agent/register`) that issues a per-agent bearer token.
- Agent heartbeat reporting (`POST /api/agent/heartbeat`) and operator/admin agent fleet monitoring (`GET /api/agents`, `GET /api/agents/{id}`) with computed online status.

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

## Registration codes and agent registration (Stage 5)

Admins create one-time registration codes; an agent redeems one to register and receives a long-lived agent token.

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /api/admin/registration-codes` | admin | Create a code. The plaintext (e.g. `7H2K-9QXM-4TBD`) is returned **once**; only its SHA-256 hash is stored. `201`, or `409` if `codes.max_active` live codes already exist. |
| `GET /api/admin/registration-codes` | admin | List codes with a derived `status` (`active`, `used`, `revoked`, `expired`). Never shows plaintext. |
| `DELETE /api/admin/registration-codes/{id}` | admin | Revoke an unused code (`200`; `404` unknown id; `409` already used). |
| `POST /api/agent/register` | anyone with a valid code | Body `{"code": "...", "name": "pc-01"}`; returns `201 {"agent_id", "token"}`. The token is shown once. |

Rules worth knowing:

- Codes are `codes.length` characters (default 12) from a 32-symbol alphabet without `I`, `L`, `O`, `U`, shown in groups of `codes.group_size` separated by dashes. Case and dashes are ignored when a code is entered.
- A code lives `codes.ttl_s` seconds (default 24 h) and works exactly once, even when several requests race.
- Unknown, malformed, used, expired and revoked codes all return the same `403 Invalid registration code`, so a caller cannot tell which case applies.
- Agent names must match `[A-Za-z0-9][A-Za-z0-9._-]*` and be at most `agents.name_max_length` characters (`422` otherwise). A name that is already taken (ignoring case) gives `409` and does **not** use up the code.
- Operators get `403` on the code endpoints; a user token on an agent endpoint, or an agent token on a user endpoint, gets `401`.
- Implemented in Stage 6: heartbeat reporting and operator agent fleet monitoring.
- Not yet implemented: the agent loop process and its bootstrap codes (Stage 7), agent removal (Stage 8), action execution (Stage 9), throttling of failed attempts (Stage 10).

## Agent heartbeat and fleet monitoring (Stage 6)

Registered agents send heartbeats with optional machine metadata; operators and admins monitor which PCs are online.

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /api/agent/heartbeat` | agent token | Records heartbeat timestamp, client IP and optional `info` dict. Returns `200 {"server_time", "interval_s", "actions": []}`. `413` if `info` exceeds `agents.info_max_bytes`. |
| `GET /api/agents` | operator, admin | Lists all registered agents with computed `online` boolean (`true` if `now - last_seen_at <= agents.online_threshold_s`, otherwise `false`). |
| `GET /api/agents/{id}` | operator, admin | Returns detailed agent profile including parsed `info` object and `online` flag (`404` if not found). |

Rules:
- Agent identity is resolved strictly from the bearer token; foreign IDs or names in the heartbeat body are ignored.
- An agent that has never sent a heartbeat is reported as `online: false` with `last_seen_at: null`.
- Timing thresholds derive from `config_settings.toml` (`heartbeat_interval_s = 30`, `online_threshold_s = 90`).
- Anonymous callers and agent tokens receive `401` on operator/admin agent viewing endpoints.

## How to test the features

### Automated

```sh
conda activate labpc
python -m pytest -q                              # everything
python -m pytest -q tests/unit/testRegistration.py   # Stage 5 only
python -m pytest -q tests/unit/testHeartbeat.py      # Stage 6 only
python -m pytest -q -k "Redeem or Expiry or Online"  # race, expiry and boundary tests
./scripts/test.sh quick                          # ruff + pyright + pytest
```

`testRegistration.py` covers: alphabet/format/uniqueness of generated codes, case- and dash-insensitive input, single use, five threads racing for one code (exactly one `201`), expiry at `codes.ttl_s - 1` versus `codes.ttl_s` (fake clock, no sleeping), identical errors for unknown/used/expired/revoked codes, no plaintext code or token in the database files, `codes.max_active`, duplicate and invalid names, revoke rules, and token separation between users and agents. `testAuth.py` holds the route authorization matrix, which fails if any route is missing from it.

With the stack running (`docker compose up --build -d`), the Stage 5 and 6 end-to-end checks are:

```sh
./scripts/e2e_stage5.sh
./scripts/e2e_stage6.sh
```

It logs in as both sample users, checks the `403`/`401` responses, creates, redeems, reuses, lists and revokes codes, then removes its throw-away agent. The complete gate (`./scripts/test.sh`, which wipes Compose volumes) runs it automatically.

### Manual walkthrough with curl

Start the stack, then fetch the CA certificate and the sample passwords:

```sh
docker compose up --build -d
CA=$(mktemp)
docker compose exec -T proxy cat /certs/ca.pem > "$CA"
ADMIN_PW=$(docker compose exec -T app sed -n 's/^admin://p' /data/sample_credentials.txt)
OPERATOR_PW=$(docker compose exec -T app sed -n 's/^operator://p' /data/sample_credentials.txt)
URL=https://localhost:8443
login() { curl -s --cacert "$CA" -H 'Content-Type: application/json' -d "{\"username\":\"$1\",\"password\":\"$2\"}" $URL/api/auth/login | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'; }
ADMIN=$(login admin "$ADMIN_PW")
OPERATOR=$(login operator "$OPERATOR_PW")
```

1. Create a code as admin. Expect `201` and a plaintext `code`:
   `curl -s --cacert "$CA" -X POST -H "Authorization: Bearer $ADMIN" $URL/api/admin/registration-codes`
2. As operator the same call returns `403`; without a token, `401`.
3. Register an agent with that code (lower case and missing dashes also work). Expect `201` with `agent_id` and `token`:
   `curl -s --cacert "$CA" -H 'Content-Type: application/json' -d '{"code":"<CODE>","name":"manual-pc"}' $URL/api/agent/register`
4. Repeat step 3: `403 Invalid registration code`. Use a made-up code: the same `403`.
5. List codes: the one you used shows `"status":"used"`, and no plaintext appears:
   `curl -s --cacert "$CA" -H "Authorization: Bearer $ADMIN" $URL/api/admin/registration-codes`
6. Create a second code, revoke it with `DELETE $URL/api/admin/registration-codes/<ID>` (`200`), then try to register with it: `403`.
7. Revoke the already-used code from step 1: `409`.
8. Try an invalid name (`"name":"bad name"`) with a fresh code: `422`, and the code still works afterwards with a valid name.
9. Check that the agent token is not accepted as a user token (`401`):
   `curl -s -o /dev/null -w '%{http_code}\n' --cacert "$CA" -H "Authorization: Bearer <AGENT_TOKEN>" $URL/api/auth/me`

The `manual-pc` agent stays registered until Stage 8 adds removal. To discard it, run `docker compose down -v` (this wipes all Compose data). Clean up with `rm -f "$CA"`.

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

The agent background loop daemon (`agent-pc-01..03`), agent removal (`DELETE /api/agents/{id}`), action execution, rate limiting and the browser dashboard are later stages. The agent Docker target remains a placeholder until Stage 7. Conda dependency solving and Docker-based integration checks must be run in an environment with Conda/Mamba and Docker installed.
