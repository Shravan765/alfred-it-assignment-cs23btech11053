#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

runImageTests() {
	docker compose --profile test run --build --rm --entrypoint python tests --version
	docker compose --profile test run --rm tests sh -c 'ruff check . && pyright && pytest -q'
}

mode=${1:-default}
if [ "$mode" = quick ]; then
	if [ "${CONDA_DEFAULT_ENV:-}" = labpc ]; then
		ruff check .
		pyright
		python -m pytest -q
	else
		runImageTests
	fi
else
	# The full gate intentionally removes named volumes before its clean-start test.
	docker compose down -v
	docker compose up --build -d
	tmp_ca=$(mktemp)
	trap 'rm -f "$tmp_ca"' EXIT
	attempt=0
	until docker compose exec -T proxy cat /certs/ca.pem > "$tmp_ca" 2>/dev/null; do
		attempt=$((attempt + 1))
		if [ "$attempt" -ge 120 ]; then
			docker compose logs
			exit 1
		fi
		sleep 1
	done
	attempt=0
	until curl --fail --silent --cacert "$tmp_ca" https://localhost:8443/health >/dev/null 2>&1; do
		attempt=$((attempt + 1))
		if [ "$attempt" -ge 120 ]; then
			docker compose logs
			exit 1
		fi
		sleep 1
	done
	./scripts/e2e_stage1.sh
	./scripts/e2e_stage2.sh
	./scripts/e2e_stage4.sh
	./scripts/e2e_stage5.sh
	./scripts/e2e_stage6.sh
	runImageTests
	echo 'Stage 0–6 validation gate passed.'
fi
