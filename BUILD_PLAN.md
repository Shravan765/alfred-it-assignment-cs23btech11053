# BUILD_PLAN.md: Lab PC Agent (Python)

**Version 2.** Changes from the chat draft (v1):

- All defaults now live in `config_settings.toml` and are read from there (§2.2, Appendix A). No Python code holds a default value.
- Dependencies are managed with a conda environment described in `environment.yml` (§2.1, Appendix B). The Docker images are built from the same file.
- Consequences of those two changes, plus a few fixes found while rewriting: a single multi-stage `Dockerfile`; `certgen` is now a shell script that makes a throw-away CA and a leaf certificate; queued actions also expire; there is no `.env` file; the pytest file pattern is configured explicitly. See the "new in v2" rows of §1.

---

## 0. How to read this plan

- **Humans:** read §1 to §3, §8 and §10.
- **An AI agent working on stage N:** read §2 (always), your stage in §7, and only the design sections that stage touches. Do not refactor other stages. Then follow §9.
- Every stage ends the same way: tests green, global gate (§8) green, `AI_USAGE.md` entry appended, commit, push.

## 1. Decisions (confirmed defaults)

| # | Topic | Decision |
|---|---|---|
| 1 | Time available | Not stated. §10 has cut lines so any stopping point is a valid hand-in. |
| 2 | Admin vs operator | Admin is a superset of operator. One permission table (`permissions.py`), so changing it is a one-line edit. |
| 3 | UI or API | API first; a tiny vanilla-JS page at `/` in Stage 10. |
| 4 | Sample credentials | Random passwords generated on first start, written to `/data/sample_credentials.txt` (mode 600). README: `docker compose exec app cat /data/sample_credentials.txt`. Never logged; restarts never regenerate them. |
| 5 | Demo agents need a code, but codes are admin-created | The server seeds one one-time code per name in `seed.bootstrap_agent_names` into the `bootstrap` volume on first boot. The three agent containers read their own file, so `docker compose up --build` works with no manual step. |
| 6 | Agent transport | Agents use the proxy over HTTPS. A one-shot `certgen` container makes a throw-away **CA + leaf certificate** (SANs: `localhost`, `proxy`, `127.0.0.1`, `::1`) into a `certs` volume. The proxy serves the leaf; agents trust only `ca.pem`. Certificate checking is never disabled. |
| 7 | Database | SQLite (WAL) on a named volume, stdlib `sqlite3`, no ORM. Every line stays explainable. |
| 8 | Action list | `clear_print_queue`, `restart_print_spooler`, `flush_dns_cache`, `get_disk_usage`. They act on a **simulated PC** (`agent.sim_root`) inside the container. No parameters, no shell. |
| 9 | Action pickup | Actions are delivered in the heartbeat reply, so pickup takes up to one interval (30 s). |
| 10 | Timing constants | Online = last heartbeat at most 90 s old. Dispatched action with no result expires after 120 s. Codes expire after 24 h. All of them live in `config_settings.toml`. |
| 11 | Removing an agent | Hard delete, cascading to its actions. Its next heartbeat gets 401; the agent wipes its token, remembers the used code hash, and idles (no crash loop). |
| 12 | Style edge cases | Classes PascalCase; module-level constants UPPER_SNAKE; test functions `testXxx`; JSON fields snake_case; dunder methods exempt. `'''` docstring on every module, class and function (one line is enough, tests included). |
| 13 | Repo timing | Create the private repo and add `adi-alfredLab` before the test. **Push nothing until the timer starts.** |
| 14 | Config (new in v2) | `config_settings.toml` at the repo root is the single source of defaults. Environment variables are used for exactly three things: `LAB_CONFIG_FILE`, `LAB_CONFIG_OVERLAY`, `AGENT_NAME`. |
| 15 | Dependencies (new in v2) | `environment.yml` is the only dependency list. Local development uses a conda env named `labpc`; Docker images install the same file with micromamba. |
| 16 | Docker build (new in v2) | One multi-stage `Dockerfile` on a micromamba base with targets `base`, `server`, `agent`, `tests`: one conda solve, shared by all three services. |
| 17 | Queued-action expiry (new in v2) | A queued action nobody collected after `actions.queued_expiry_s` becomes `expired`, so a command for a PC that was off does not fire hours later. |
| 18 | Test timing (new in v2) | Tests never sleep. They use an injected fake clock and, where values must differ (cheap scrypt, short intervals), an **overlay** TOML or an `overrides` dict. |
| 19 | Agent identity (new in v2) | `AGENT_NAME` env var + code file `<bootstrap_dir>/<AGENT_NAME>.code`. After a successful registration the agent stores the code's hash and never tries that code again. |

**Residual assumptions (not blocking; tell me if wrong):**
1. The grading machine has outbound internet during `docker compose up --build` (conda-forge, Docker Hub). Nothing in the spec forbids it, but it is the one external dependency of the build.
2. `8443:8443` is published on all interfaces. Restricting it to `127.0.0.1:8443:8443` is a one-line change if wanted.
3. If an agent's `/state` volume is lost while the server's data survives, that agent cannot re-register with its old (used) code; an admin must issue a new code and drop it in the bootstrap file. This is documented in the README as a known limitation.

---

## 2. Ground rules (apply to every stage)

### 2.1 Conda environment

```bash
conda env create -f environment.yml          # once
conda activate labpc
conda env update -f environment.yml --prune  # after editing environment.yml
```

Rules:

1. `environment.yml` (Appendix B) is the **only** dependency list. Never `pip install` or `conda install` something without adding it to the file.
2. To add a package: add it with a `#` reason comment, run the update command, rebuild the images (`docker compose build`), add a row to the **Dependencies** table in `AI_USAGE.md`.
3. Everything must come from `conda-forge`. If a package is only on PyPI, stop and ask the human before adding a `pip:` section.
4. **Stage 0 pins versions.** The template pins only Python. After the first successful solve, run `conda list --export` (or `micromamba list` in the image), pin each package in `environment.yml` to the solved `major.minor` (for example `fastapi=0.x`), and commit. Do not guess version numbers from memory; copy them from the solved environment.
5. Docker images install the file into the micromamba **base** environment and set `ENV PATH` explicitly to that environment's `bin`, so `python` works for `CMD`, `HEALTHCHECK` and `docker compose exec` alike (do not rely on the entrypoint activating anything). Stage 0 verifies this with a one-line check (`docker run --rm --entrypoint python <image> --version`).
6. `scripts/test.sh` runs everything inside the `tests` image, so a machine without conda can still run the tests. `scripts/test.sh quick` uses the active `labpc` env if there is one, otherwise falls back to the image.

### 2.2 Configuration: `config_settings.toml`

The file is in Appendix A. Rules:

1. **No defaults in code.** `loadSettings()` raises `ConfigError` for a missing key, an unknown key, a wrong type, a value out of range, or a violated cross-field rule. The error lists **all** problems, not only the first.
2. Parsing uses stdlib `tomllib` (opened in binary mode). Result: frozen dataclasses, one per section (`settings.agents.online_threshold_s`). No dictionary access outside `config.py`.
3. Signature: `loadSettings(config_path=None, overlay_path=None, overrides=None)`. Paths default to env `LAB_CONFIG_FILE` and `LAB_CONFIG_OVERLAY`. `overrides` is a nested dict for tests. Merge order: base file, then overlay, then `overrides`. Overlay and overrides may only touch existing keys; arrays are replaced whole.
4. Cross-field rules (all checked in `loadSettings`):
   - `agents.online_threshold_s >= 2 * agents.heartbeat_interval_s`
   - `actions.dispatch_expiry_s >= agent.action_timeout_s + agents.heartbeat_interval_s`
   - `codes.length % codes.group_size == 0` and `codes.length >= 12`
   - `actions.history_default_limit <= actions.history_max_limit`
   - `scrypt_n` is a power of two; `agent.backoff_initial_s <= agent.backoff_max_s`
   - `actions.enabled_kinds` is a non-empty subset of the code registry (`ACTION_KINDS`)
   - `seed.users` contains at least one `admin` and one `operator`; usernames and bootstrap agent names are unique and valid
   - `agent.server_url` starts with `https://`
5. Server and agent use the same loader, `shared/config.py`, which both images copy; neither has any defaults of its own. Heartbeat interval: the agent starts from `agents.heartbeat_interval_s` and then obeys the `interval_s` in each heartbeat reply.
6. **Config keys grow with the stages** (table below). The `UNIMPLEMENTED_KEYS` set in `tests/unit/testConfig.py` lists keys whose stage has not been reached; each stage deletes its keys from that set. The ratchet test fails if a key is neither read by code nor in the set, or is in the set but already read. At the end of Stage 10 the set is empty, which proves that every default in the file is actually used.

| Stage | Keys that start being read |
|---|---|
| 0 | `loadSettings` itself, all sections validated (but not all used yet) |
| 1 | `logging.level`, `logging.include_actor` |
| 2 | `paths.data_dir`, `paths.database_file`, `database.*` |
| 3 | `auth.*`, `paths.credentials_file`, `seed.password_length`, `seed.users` |
| 5 | `codes.*`, `agents.name_max_length` |
| 6 | `agents.heartbeat_interval_s`, `agents.online_threshold_s`, `agents.info_max_bytes` |
| 7 | `paths.bootstrap_dir`, `seed.bootstrap_agent_names`, `agent.server_url`, `agent.ca_bundle`, `agent.state_dir`, `agent.bootstrap_dir`, `agent.connect_timeout_s`, `agent.read_timeout_s`, `agent.backoff_*` |
| 9 | `actions.*`, `agent.action_timeout_s`, `agent.sim_root`, `agent.demo_spool_jobs`, `agent.disk_usage_path` |
| 10 | `throttle.*`, `limits.max_body_bytes` |

7. In tests, **never hard-code a number that comes from the config.** Write `settings.agents.online_threshold_s + 1`, not `91`. This proves the code reads the config and keeps tests valid when the defaults change. (Exactly one test in Stage 6 also asserts the shipped defaults are 30 / 90, because those are required by the task.)
8. Tests that need different values use `tests/config_overlay.unit.toml` (cheap scrypt such as `scrypt_n = 1024`, short TTLs) or an `overrides` dict. The shipped file is never edited by tests.

### 2.3 Coding style (checked automatically in Stage 0)

| Item | Rule |
|---|---|
| Indentation | **One tab** per level. No spaces for indentation. |
| Functions and methods | `camelCase` (`loadSettings`, `hashPassword`). Dunders (`__init__`) exempt. |
| Variables, arguments, attributes | `snake_case`. Module-level constants `UPPER_SNAKE`. |
| Classes | `PascalCase`. |
| Function / class / module description | A docstring written with `'''` (not `"""`). Every module, class and function has one; one line is enough. |
| Block-level comments | `#` comments above the block they describe. |
| Tests | `testXxx` functions in `testXxx.py` files. Fixtures are functions, so they are camelCase too. |
| Names forced by a framework | Add `# style: external-name` on the line and mention it in the AI_USAGE entry. Keep these to a minimum. |

Notes:

- pytest's default file pattern is `test_*.py`, which does not match `testStyle.py`. `pytest.ini` therefore sets `python_files = test*.py` and `python_functions = test*`.
- **Do not run `ruff format`:** it normalises docstring quotes to `"""` and would undo the required style. Use `ruff check` only (default rule set; do not enable the `W` or `Q` families). The style checker in Stage 0 is the authority.

---

## 3. High-level design

```
 host :8443 (HTTPS)
      │
 ┌────▼─────┐  http (docker network only)   ┌─────────┐   ┌───────────────┐
 │  proxy   ├──────────────────────────────►│   app   ├──►│ /data/lab.db  │ volume app_data
 │ (Caddy)  │                               │ FastAPI │   └───────────────┘
 └────▲─────┘                               └────▲────┘
      │ https://proxy:8443 (trusts ca.pem)       │ writes bootstrap codes
 ┌────┴────┐ ┌────────┐ ┌────────┐               │
 │ agent   │ │ agent  │ │ agent  │◄── bootstrap volume (read-only)
 │ pc-01   │ │ pc-02  │ │ pc-03  │   each has its own /state volume
 └─────────┘ └────────┘ └────────┘
 certgen (one-shot) → certs volume → proxy (leaf cert+key), agents (ca.pem, read-only)
 tests (compose profile "test"; plain `up` never starts it)
 config_settings.toml mounted read-only into app and agents at /config/
```

**Only `proxy` has `ports:`.** `app` uses `expose` only, and the database file is reachable only through the `app_data` volume mounted in `app`.

**Principals**

- `admin` and `operator` are rows in `users` and use session tokens.
- `agent` is a row in `agents` and uses a long-lived agent token issued at registration.
- Three separate auth dependencies (`currentUser`, `requireCapability`, `currentAgent`) mean a token of one kind never works on another's endpoints.

**Lifecycle**

1. Admin calls `POST /api/admin/registration-codes`; the plaintext code is shown once.
2. Agent calls `POST /api/agent/register {code, name}` and receives `{agent_id, token}`. The code is redeemed atomically and works once.
3. Every `heartbeat_interval_s` the agent calls `POST /api/agent/heartbeat`. Reply: `{server_time, interval_s, actions:[{id, kind}]}`. Queued actions are claimed (`dispatched`) in that same call.
4. The agent runs each action from its own fixed registry and calls `POST /api/agent/actions/{id}/result {ok, output}`.
5. Operators see `online` (computed from `last_seen_at`) plus the queue and history.

**Key principles**

- The server sends **action names only**, never commands. The agent maps names to built-in Python functions.
- Times are UTC epoch integers in the database and ISO-8601 in the API. All time comes from an injectable `clock` (`clock.py`), so tests never sleep.
- Secrets are stored as hashes: tokens and codes as SHA-256 (they are high-entropy random values), passwords with scrypt plus a per-user salt.
- Expiry is **lazy**: `expireStale(now)` runs inside heartbeat, result and list calls. No background threads.
- User API tokens travel in `Authorization: Bearer`, so there are no cookies and no CSRF surface. The dashboard keeps its token in `sessionStorage`.

## 4. Data model

| Table | Columns (key ones) |
|---|---|
| `users` | id, username UNIQUE, password_hash, role CHECK(admin/operator), created_at |
| `sessions` | token_hash PK, user_id FK CASCADE, created_at, expires_at |
| `registration_codes` | id, code_hash UNIQUE, created_by FK users ON DELETE SET NULL, created_at, expires_at, used_at NULL, used_by_agent FK agents ON DELETE SET NULL, revoked_at NULL |
| `agents` | id, name UNIQUE NOCASE, token_hash UNIQUE, registered_at, last_seen_at NULL, last_ip, info_json |
| `actions` | id, agent_id FK CASCADE, kind, status CHECK(queued/dispatched/succeeded/failed/expired), created_by FK users ON DELETE SET NULL, created_at, dispatched_at, finished_at, output |
| `meta` | key PK, value (`schema_version`, `boot_count`, `bootstrap_done`) |

Every connection runs `PRAGMA foreign_keys=ON`, `journal_mode` and `busy_timeout` from the config. Write paths use `BEGIN IMMEDIATE` through `runInTransaction()`; connections are opened with `isolation_level=None` so transactions are always explicit. The server checks at start-up that SQLite is version 3.35 or newer (needed for `RETURNING`).

## 5. API surface

| Endpoint | Who |
|---|---|
| `GET /health` | anyone (runs `SELECT 1`; 503 if the DB is down) |
| `POST /api/auth/login`; `POST /api/auth/logout`, `GET /api/auth/me` | login: anyone; others: any signed-in user |
| `POST/GET /api/admin/registration-codes`, `DELETE …/{id}` (revoke) | admin |
| `DELETE /api/agents/{id}` | admin |
| `GET /api/agents`, `GET /api/agents/{id}` | operator, admin |
| `GET /api/actions/catalog`, `POST /api/agents/{id}/actions`, `GET /api/agents/{id}/actions`, `GET /api/actions/{id}` | operator, admin |
| `POST /api/agent/register` | anyone holding a valid code |
| `POST /api/agent/heartbeat`, `POST /api/agent/actions/{id}/result` | agent token |
| `GET /api/audit` (Stage 10, optional) | admin |

Status codes: 401 not authenticated, 403 wrong role, 404 missing, 409 conflict/duplicate/queue full, 413 body too large, 422 validation, 429 throttled.

## 6. Low-level layout

```
docker-compose.yml        # services: certgen, proxy, app, agent-pc-01..03, tests(profile)
Dockerfile                # multi-stage: base → server | agent | tests   (build context = repo root)
environment.yml           # ONLY dependency list           (Appendix B)
config_settings.toml      # ONLY defaults                  (Appendix A)
pytest.ini  ruff.toml  .gitignore  .dockerignore
README.md  BUILD_PLAN.md  AI_USAGE.md
proxy/Caddyfile           # global {auto_https off, admin off}; :8443 { tls /certs/leaf.pem /certs/leaf.key; reverse_proxy app:8000 }
certgen/{Dockerfile,gen.sh}   # alpine + openssl; idempotent: does nothing if files exist
shared/
  config.py        ConfigError, section dataclasses, loadSettings()  (used by server AND agent)
server/app/
  main.py          createApp() (uvicorn --factory); lifespan: loadSettings → initSchema → seedUsers → seedBootstrapCodes
  clock.py         nowEpoch() (overridable in tests)
  db.py            getConnection(), initSchema(), dbConn() dependency, runInTransaction()
  schema.sql
  security.py      hashPassword(), verifyPassword(), newToken(), hashToken(), newRegistrationCode(), CODE_ALPHABET
  permissions.py   CAPABILITIES = {capability: {roles}}, requireCapability()
  deps.py          currentUser(), currentAgent()
  logging_mw.py    one line per request: ts method path(no query) status ms ip actor
  seed.py          seedUsers(), seedBootstrapCodes()
  cli.py           backup / restore
  actions_catalog.py   ACTION_KINDS (server's single source of truth for names)
  routers/{health,auth,codes,agents,actions,agentApi}.py
agent/agent/
  state.py (atomic JSON, 0600)  client.py (httpx; injectable)
  loop.py (state machine; injectable sleep/clock)  actions.py (registry + simulated executors)  main.py
scripts/{test.sh, backup.sh, restore.sh, gate.sh, e2e_*.sh}
tests/{conftest.py, config_overlay.unit.toml, unit/, integration/, e2e/}
```

**Docker and compose notes**

- Build context is the repo root (the Dockerfile needs `environment.yml`); `.dockerignore` excludes `.git`, `backups/`, `__pycache__`.
- `base` target: micromamba image (pin an explicit tag chosen in Stage 0), `COPY environment.yml`, install into base, `micromamba clean --all --yes`, set `ENV PATH` (§2.1 rule 5), `PYTHONUNBUFFERED=1`. Then, as root, create `/data`, `/state`, `/bootstrap` and `/var/lib/labpc/sim` owned by the image's non-root user, and switch back to that user **before** `VOLUME`/mounts so named volumes inherit writable ownership.
- `server` target: copies `shared/`, `server/` and `config_settings.toml` (the mounted copy overrides it). Command: `uvicorn app.main:createApp --factory --host 0.0.0.0 --port 8000 --no-access-log --proxy-headers --forwarded-allow-ips='*'`. The `*` is safe because `app` is reachable only from the proxy network. Healthcheck: `python -c` calling `/health` with `urllib`.
- `agent` target copies `shared/`, `agent/` and the config the same way. `tests` copies everything, including `tests/` and `scripts/`. `PYTHONPATH` includes `/srv/shared`, `/srv/server` and `/srv/agent`.
- `certgen` writes `ca.pem`, `leaf.pem` (leaf + chain) and `leaf.key`; leaf lifetime 825 days. It skips generation if the files already exist, so `down`/`up` keeps the same certificates.
- `depends_on`: `app` waits for `certgen` (`service_completed_successfully`); `proxy` waits for `certgen` and a healthy `app`; agents wait for a healthy `app` and a started `proxy`. Agents also retry with backoff, so ordering is a convenience, not a requirement.
- The gate starts the stack with `docker compose up --build -d` and then **polls** `https://localhost:8443/health` (up to 120 s). It does not rely on `up --wait`, whose treatment of finished one-shot containers differs between Compose versions.
- No `.env` file is needed anywhere. `docker compose up --build` from a clean clone must work with nothing but the repo.

---

## 7. Stages

Priority order: easiest and most foundational first (guardrails and config, walking skeleton, persistence, the three roles, mandatory backup/restore, registration and heartbeat), then operators and actions, then polish. Each stage has its own **tests** and a **done-when** line. Tests are written first.

### Stage 0: Guardrails, conda, config (about 8%)

**Build**

- Repo files: `.gitignore` (`backups/`, `__pycache__`, `.pytest_cache`), `.dockerignore`, `pytest.ini`, `ruff.toml`, README and `AI_USAGE.md` skeletons.
- Create `environment.yml` and `config_settings.toml` **verbatim from the appendices**, then pin versions (§2.1 rule 4).
- Create the conda env and check `python -c "import tomllib, fastapi, httpx"`.
- `shared/config.py` with `ConfigError`, the section dataclasses and `loadSettings()` (§2.2).
- `Dockerfile` with `base` and `tests` targets; compose `tests` service (profile `test`); `scripts/test.sh` that builds and runs the `tests` target.
- `tests/conftest.py` with fixtures: `settings` (shipped file + `config_overlay.unit.toml`), `fakeClock`.

**Tests**

- `tests/unit/testStyle.py` is a real checker over every `.py` file, using `tokenize` and `ast`:
  - indentation tokens are tabs only
  - function and method names match `^[a-z][a-zA-Z0-9]*$` (dunders exempt)
  - assigned names, arguments, loop and `with` targets match `^[a-z_][a-z0-9_]*$` (module-level `UPPER_SNAKE` allowed)
  - classes are PascalCase
  - every module, class and function has a docstring whose source token starts with `'''` (found via `tokenize`, because `ast` cannot tell quote styles apart)
  - lines carrying `# style: external-name` are skipped
- The same file has **self-tests** with deliberately bad fixture strings (space indent, `def snake_func`, `camelVar = 1`, a `"""` docstring, a missing docstring). Each must be flagged, and one good fixture must pass.
- `tests/unit/testConfig.py`:
  - the shipped file loads; the keys in the TOML and in the dataclasses match **exactly in both directions**
  - missing key, unknown key (typo), wrong type, out-of-range value: each raises `ConfigError` naming the key; two problems in one file are both reported
  - every cross-field rule in §2.2 rejects a violating file
  - overlay overrides a scalar, cannot add an unknown key, replaces arrays; `overrides` dict beats overlay
  - `LAB_CONFIG_FILE` is honoured; a missing file gives a clear error
  - no string-valued key whose name contains `password`, `secret` or `token`
  - the ratchet test (`UNIMPLEMENTED_KEYS`, §2.2 rule 6)
  - Python version is 3.11 or newer
- Gate: `ruff check`, `./scripts/test.sh` green inside the image, and the PATH check from §2.1 rule 5.

**Done when:** the conda env builds, `./scripts/test.sh` is green, and the self-tests prove the style checker can fail.

### Stage 1: Walking skeleton (about 10%). First push once the timer starts.

**Build:** `certgen`, `proxy`, `app` (`/health`, `logging_mw`), compose with healthchecks and `depends_on`. Server stage of the `Dockerfile`.

**Tests**

- Unit: `/health` is 200 with no auth.
- Unit: exactly one log line per request, including a 404 and a request whose handler raises (500). The line has method, path **without query string**, status, duration, ip.
- Unit: log lines never contain `Authorization`, any token, or a query string (send `?token=abc` and assert it is absent).
- Unit: `logging.include_actor = false` (via `overrides`) removes the actor field; `logging.level = "ERROR"` suppresses request lines (proves the keys are really read).
- E2E (shell): `curl --cacert <ca> https://localhost:8443/health` returns 200; `curl -k` is not needed. Plain `http://localhost:8443` does not return 2xx.
- E2E (shell): `curl localhost:8000` is refused. The gate **fails** if `docker ps --format '{{.Ports}}'` shows any host-published port other than 8443.
- E2E (shell): `docker compose logs app` contains one line for the `/health` request just made.
- Static check: no `verify=False` and no `ssl._create_unverified_context` anywhere in the repo.

**Done when:** a clean `docker compose up --build` serves `/health` on 8443 and nothing else is published.

### Stage 2: Database and persistence (about 7%)

**Build:** `db.py`, `schema.sql`, `meta.boot_count` incremented at start-up, `/health` checks the DB, the `app_data` volume, SQLite-version check.

**Tests**

- Schema init is idempotent and records `schema_version`.
- Foreign keys are really enforced (inserting an orphan fails; deleting a parent cascades or sets NULL as in §4).
- 8 threads inserting concurrently never raise "database is locked".
- `database.busy_timeout_ms` and `journal_mode` from settings are what the connection reports (`PRAGMA`).
- `/health` returns 503 when the DB path becomes unusable (monkeypatch).
- Shell: `boot_count` is N after `up`, N+1 after `down && up`, and 1 after `down -v && up`.

### Stage 3: Users, roles, auth, sample users (about 8%)

**Build:** login, sessions, `/me`, `requireCapability`, `seedUsers()` for every `seed.users` entry, the credentials file.

**Tests**

- scrypt round-trip, wrong password, and parameters in the stored hash equal `settings.auth.scrypt_*`; salts differ per user.
- Unknown-user login runs a dummy verify and returns a response identical to a wrong password.
- Session expires exactly at `session_ttl_s` (fake clock, boundary ±1 s); logout invalidates the token; the DB holds only a token hash (read the DB bytes).
- `seedUsers()` is idempotent, never changes existing passwords, never rewrites the credentials file, and the file has mode 600 and one line per user.
- A user listed in `seed.users` via `overrides` is created (proves seeding is config-driven).
- **Authorization matrix test (grows every stage):** iterate every route in `app.routes` × {anonymous, operator, admin, agent} and assert the expected status. It **fails if any route is missing from the matrix**, which catches forgotten auth.
- Shell: the credentials in the file still work after `down && up`.

### Stage 4: Backup and restore (about 8%)

Mandatory, so it is done early. It needs real data and users already exist.

**Build**

- `cli backup <out>` uses the `sqlite3` online backup API.
- `cli restore <in>` runs `integrity_check`, checks `schema_version`, copies to a temp file in `data_dir`, `os.replace`s it, and removes `-wal` and `-shm`.
- `scripts/backup.sh` runs `cli backup` inside `app` via `exec` and then `compose cp` to `./backups/lab-<timestamp>.db` (mode 644).
- `scripts/restore.sh <file>` stops `app`, runs `docker compose run --rm --no-deps -v ./backups:/backups:ro app python -m app.cli restore /backups/<file>`, then starts everything and polls `/health`.
- README note: restore replaces the database only; the credentials file is not part of the backup, so after restoring a backup from a different install the file can be stale.

**Tests**

- Round-trip through temp files.
- A backup taken **while a writer thread is inserting** is internally consistent (`integrity_check` returns ok).
- Restoring a text file, a truncated DB, or a DB with a wrong `schema_version` is rejected and **leaves the live DB untouched**.
- Shell: record a marker (login count or a new session), back up, mutate, restore, and verify the original state is back and `/health` is 200.

**Cut line A:** a valid system that starts, has roles and users, and meets every mandatory requirement.

### Stage 5: Registration codes and agent registration (about 8%)

**Build:** admin code endpoints; `POST /api/agent/register`; `currentAgent`. Codes use the 32-character alphabet in `security.CODE_ALPHABET` (Crockford-style: digits and letters without I, L, O, U), `codes.length` characters, grouped by `codes.group_size`; input is normalised (case, dashes).

**Tests**

- **Concurrency:** 5 threads redeem the same code; exactly one gets 201, the rest get 4xx (redeem is `UPDATE … WHERE used_at IS NULL AND revoked_at IS NULL AND expires_at > :now`, rowcount checked).
- Expired (boundary ±1 s on `codes.ttl_s`), revoked and used codes get the same generic error as an unknown code.
- `CODE_ALPHABET` has 32 unique characters; generated codes match the length and grouping from settings; two thousand generated codes show no duplicates.
- Plaintext code and token are not in the DB (grep the DB bytes). The token is returned once only.
- `codes.max_active` is enforced (409). Duplicate name gives 409; name longer than `agents.name_max_length` or with bad characters gives 422.
- A user token on an agent endpoint gives 401, and an agent token on a user endpoint gives 401.
- Listing codes shows status but never plaintext. Operator creating a code gives 403.
- Matrix test updated.

### Stage 6: Heartbeat, server side (about 8%)

Chunks: **6a** endpoint, store `last_seen_at`, `last_ip`, `info_json`. **6b** `isOnline()`. **6c** operator list and detail.

**Tests**

- 6a: identity comes from the token only (the body has no id; a foreign id in the body is ignored or rejected, and agent A can never touch B). `info` larger than `agents.info_max_bytes` gives 413 or 422. Invalid or removed token gives 401. The reply contains `interval_s == settings.agents.heartbeat_interval_s`.
- 6b: boundaries with the injected clock: `online_threshold_s - 1`, `online_threshold_s` and `+ 1` give online, online, offline. An agent that never sent a heartbeat is offline.
- 6c: the list shows correct online flags for a mixed fleet; an operator can read it; anonymous gets 401.
- The one test that asserts the shipped defaults are 30 and 90 (task requirement).

### Stage 7: The agent and the demo fleet (about 14%)

Chunks: **7a** config and atomic state file. **7b** client (CA bundle, timeouts, backoff with jitter, injectable `http`). **7c** loop state machine. **7d** Dockerfile `agent` target, compose `agent-pc-01..03` (each with `AGENT_NAME`, own `/state` volume, read-only `certs` and `bootstrap` volumes), and `seedBootstrapCodes()`.

State machine (the loop takes an injected `sleep` and `clock`):

```
UNREGISTERED: read <bootstrap_dir>/<AGENT_NAME>.code
    no file, or its hash == consumed hash  → sleep backoff_max_s, retry (idle)
    register OK  → save {agent_id, token, consumed_code_hash} atomically → RUNNING
    4xx          → record consumed hash, idle     5xx / network → backoff, retry
RUNNING: heartbeat every interval_s (from the last reply)
    200  → run each returned action, post its result
    401  → wipe token, keep consumed hash → UNREGISTERED (idle)
    5xx / network → backoff with jitter (capped), keep state
```

**Tests**

- Unit: the state file is written atomically (a simulated crash mid-write leaves the old file) and has mode 600 and the directory 700.
- Unit: backoff sequence follows `backoff_initial_s`, doubles, caps at `backoff_max_s`, and jitter stays within ±`backoff_jitter` (sleep injected, `random` seeded).
- Unit: after a restart the agent **does not re-use** the code; with a consumed hash and the same code file it idles without calling the server.
- Unit: a 401 clears the token but keeps the consumed hash.
- Unit: the client refuses a non-`https` URL and uses `agent.ca_bundle`.
- **Integration (in-process, no network):** the real agent client points at the real FastAPI `TestClient`. It registers, sends 3 heartbeats with the fake clock, and the operator list shows it online; after `online_threshold_s + 1` of fake time it shows offline.
- E2E: after `down -v && up --build`, `GET /api/agents` shows 3 online within 35 s (this uses the real 30 s interval).
- E2E: from the `tests` container, a request trusting `ca.pem` succeeds and one using the system CAs fails certificate verification (proves TLS is genuinely validated).
- E2E (marked `slow`, run by `./scripts/test.sh full`): `docker compose stop agent-pc-02` makes it offline within 95 s.

**Cut line B:** registration and heartbeat are demonstrable end to end.

### Stage 8: Admin removal and revocation (about 5%)

**Tests**

- `DELETE /api/agents/{id}` cascades to the agent's actions.
- The removed agent's next heartbeat gets 401; the agent wipes its token and idles without crash-looping (E2E: remove pc-03, watch its log show a single "removed, idle" message, not a repeating error).
- Operator gets 403, a missing id gets 404, and the name is re-usable afterwards.
- Revoked code cannot be redeemed; revoking a used code gives 409.

### Stage 9: Actions (about 14%)

Chunks: **9a** catalog and enqueue. **9b** claim on heartbeat. **9c** result endpoint. **9d** agent executors. **9e** history views. **9f** expiry.

- Claim is one `BEGIN IMMEDIATE` transaction: `UPDATE actions SET status='dispatched', dispatched_at=:now WHERE id IN (SELECT id FROM actions WHERE agent_id=:a AND status='queued' ORDER BY id LIMIT :n) RETURNING id, kind`. `RETURNING` order is not guaranteed, so sort by id in Python.
- Executors act only on `agent.sim_root`: `clear_print_queue` removes files in `<sim_root>/spool/` and reports "removed N jobs"; `restart_print_spooler` increments a counter in `spooler.json`; `flush_dns_cache` empties `dns_cache.txt`; `get_disk_usage` reports `shutil.disk_usage(agent.disk_usage_path)`.

**Tests**

- Unknown kind gives 422, including injection-style strings; a kind removed from `enabled_kinds` (via `overrides`) gives 422.
- Target agent missing or removed gives 404. The `max_queued_per_agent + 1`th queued action gives 409.
- **Concurrent heartbeats never double-dispatch** (threads); claim size never exceeds `claim_batch_size`; FIFO order.
- Only the owning agent can report a result, and only for a `dispatched` action. A duplicate report gives 409; another agent's action gives 404.
- Output longer than `output_max_bytes` is cut on a character boundary and ends with `[truncated]`.
- Expiry with the fake clock: `dispatched` + `dispatch_expiry_s` gives `expired` and a late result is rejected; a `queued` action older than `queued_expiry_s` becomes `expired` and is never handed out.
- Executors: `clear_print_queue` removes only regular files inside the spool folder; a symlink pointing outside is **not** followed and its target survives. An exception or timeout (`agent.action_timeout_s`) becomes `failed` with a message. `demo_spool_jobs` creates the fake jobs at start-up.
- Agent refuses an action name not in its registry and reports `failed`.
- E2E: operator sends `clear_print_queue` to pc-01; within about 35 s it is `succeeded` with "removed 5 jobs", and a second send reports "removed 0 jobs".

### Stage 10: Hardening and polish (about 10%, in this priority order)

1. Minimal `/` dashboard: login, PC table with online dot, action buttons; for admins, codes and remove. JS and CSS are separate static files so a strict `Content-Security-Policy` works.
2. Final README: how to run, how to get sample credentials, how to test, backup and restore, conda workflow, the config file, architecture, decisions, known limitations (§1 residual assumptions).
3. Login and register throttling (`throttle.*`) returning 429, plus the `limits.max_body_bytes` check (413). Tests use `overrides` with small values and the fake clock for the window.
4. Security headers and a body-size cap in the Caddyfile (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, CSP; no HSTS because the certificate is self-signed).
5. `GET /api/audit` (who sent what to whom).
6. **Clean-machine rehearsal:** fresh `git clone` into a temp directory, `docker compose up --build`, the gate, and the smoke test. Confirm `UNIMPLEMENTED_KEYS` is empty.
7. **Hand-in:** `git rev-parse HEAD`, paste the repository URL and commit id into the hand-in box, and save again after every later push.

---

## 8. Validation and testing

**One command: `./scripts/test.sh`** (and `./scripts/test.sh quick` for unit and integration tests only, `./scripts/test.sh full` to include `slow` tests). It runs in order:

1. `ruff check` and the style checker.
2. Unit and integration tests: `docker compose --profile test run --rm --build tests`.
3. `docker compose down -v && docker compose up --build -d`, then poll `/health` through the proxy for up to 120 s. **This wipes the compose volumes**, so never run the gate against data you want to keep.
4. Shell checks: port exposure, health through the proxy, plain-HTTP behaviour, persistence round trip (`down && up` keeps data and `boot_count` rises by one).
5. E2E pytest (marker `e2e`) inside the `tests` container against `https://proxy:8443`.
6. Backup and restore round trip.
7. Summary, exit non-zero on any failure.

**Requirement → proof map**

| Mandatory requirement | Verified by |
|---|---|
| Proxy, HTTPS on 8443, nothing else published | Stage 1 shell checks, rerun in every gate |
| Data survives `down`/`up` | Stage 2 `boot_count` check and Stage 3 credentials round trip |
| `/health` without sign-in; one log line per request | Stage 1 and Stage 2 unit tests |
| Backup and restore scripts | Stage 4 unit and shell tests |
| Automated tests, one command | `./scripts/test.sh` |
| Sample users for every role, documented | Stage 3 tests plus the README command |
| `docker compose up --build` from a clean clone | Stage 10 rehearsal |
| Defaults come from `config_settings.toml` | Stage 0 `testConfig`, ratchet test, "never hard-code config numbers" rule |
| Dependencies in `environment.yml` | Docker build itself (images are built only from that file) |

**Per-stage validation checklist (the agent working on the stage runs this before committing)**

1. Tests for the stage were written first and failed for the right reason before the code existed.
2. `./scripts/test.sh quick` is green; then the full `./scripts/test.sh` is green.
3. For every new test, break the code on purpose (flip a comparison, remove a `WHERE` clause) and confirm the test fails. Revert. This guards against tests that cannot fail.
4. `git status` shows no secrets, no `backups/`, no `.db` files.
5. New config keys were added to the TOML **with a comment**, to the dataclass, and removed from `UNIMPLEMENTED_KEYS`.
6. New packages are in `environment.yml` with a reason, and in `AI_USAGE.md` Dependencies.
7. README sections for the stage are updated.
8. `AI_USAGE.md` entry appended (§9).

**Rules for tests:** no `time.sleep` (inject the clock or the sleep function), no real network except in E2E, no shared state between tests (each test gets a fresh temp database), threaded tests carry a `pytest-timeout` limit, and ports are never hard-coded outside E2E.

**Manual smoke test (goes into the README)**

1. `docker compose exec app cat /data/sample_credentials.txt`
2. `curl --cacert <ca.pem> https://localhost:8443/health`, then log in as admin and as operator.
3. `GET /api/agents` shows 3 agents online (within about 35 s of start).
4. Operator sends `clear_print_queue` to pc-01.
5. Poll the action until `succeeded`.
6. Operator tries `POST /api/admin/registration-codes` and gets 403.
7. Admin removes pc-03.
8. pc-03's next heartbeat is rejected and its agent goes idle.
9. `./scripts/backup.sh`, change something, `./scripts/restore.sh <file>`, and confirm the change is gone.

## 9. Rules for every AI agent working on a stage

1. Read only §2, your stage in §7 and the design sections it touches. Do not refactor other stages.
2. Write the tests first. Run the validation checklist (§8) before committing.
3. Follow §2.3 exactly: one tab, camelCase functions, snake_case variables, `'''` docstrings, `#` block comments.
4. Defaults live only in `config_settings.toml` (§2.2). Dependencies live only in `environment.yml` (§2.1).
5. Prefer the standard library and short functions. Every line must be explainable by the human.
6. Never commit secrets, `.db` files or `backups/`.
7. Update README sections for anything you add.
8. Append to `AI_USAGE.md` **before** the commit, in the format below.

### How to write `AI_USAGE.md`

One append-only file. Truthful and concise. No secrets and no full chat dumps. Write entries in the first person of the human who hands in the work, but never claim a check was done that was not (use "pending" for the human's own review until it is done). Layout:

```markdown
# AI_USAGE

## Tools used
| Tool / model | Used for | Stages |
(one row per tool; add a row the first time a tool is used)

## Dependencies
| Package | Where | Reason (one line) | Added in entry |
(mirror of environment.yml: one row per package, plus any non-conda source)

## Log
(append-only; one entry per commit-sized chunk)

### [Stage N / chunk] Short title: <ISO date and time>
- Tool/model:
- Task given (1 to 3 lines of intent, not the raw prompt):
- What the AI produced (files, approximate lines):
- What I changed or rejected, and why:
- Config / dependency changes (TOML keys added or changed; packages added):
- Verification run (commands + result; say "not run" if not run):
- Explain-it notes (for each non-trivial function or decision, 1 to 2 lines on WHY.
  Example: "redeem uses UPDATE…WHERE used_at IS NULL and checks rowcount, so
  two racing requests cannot both succeed"):
- Sources copied or adapted (docs, StackOverflow, examples; "none" if none):
- Known gaps / risks:


Optional gate step: `scripts/checkAiUsage.py` verifies that each completed stage id appears in the Log and that every package in `environment.yml` appears in the Dependencies table.

## 10. Priorities, time and cut lines

Rough time weights: S0 8%, S1 10%, S2 7%, S3 8%, S4 8%, S5 8%, S6 8%, S7 14%, S8 5%, S9 14%, S10 10%.

- **Cut line A (after S4):** a valid system that starts, has roles and users, and satisfies every mandatory item.
- **Cut line B (after S7):** adds registration, heartbeat and online status.
- **Full scope (after S9):** adds removal and actions.

If time is short, skip the Stage 10 extras but never skip the clean-clone rehearsal and the hand-in. A small system that starts from a clean clone beats a larger one that does not. Push at least every 30 minutes, and re-save the commit id in the hand-in box after each push.

---

## Appendix A: `config_settings.toml`

Create this file at the repository root **verbatim** in Stage 0. It was syntax-checked with Python 3.12's `tomllib` and its cross-field rules were checked against the defaults.

```toml
{{CONFIG_TOML}}
```

## Appendix B: `environment.yml`

Create this file at the repository root in Stage 0, then pin versions as described in §2.1 rule 4.

```yaml
{{ENVIRONMENT_YML}}
```