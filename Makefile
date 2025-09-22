.PHONY: init dev run-bot lint fmt test doctor dirs migrate seed clean-db fix-eol lint-ci cov-summary \
	test-dirs test-migrate test-backup test-db-from-prod run-bot-test clean-test-db test-env

PY=poetry run
DEV_DIRS=var var/materials var/submissions var/exports var/tmp var/logs

init: dirs
	poetry install

dev: init
	@echo "Dev env ready"

run-bot:
	$(PY) python -m app.bot.main

# --- Test environment helpers ---

# Create isolated test var dirs so backups/state don't mix with dev
test-dirs:
	@mkdir -p var/test var/test/materials var/test/submissions var/test/backup/manifests var/test/backup/archives var/snapshots

# Run migrations against test DB (uses .env + .env.test overrides)
test-migrate: test-dirs
	@bash -lc 'set -a; source .env 2>/dev/null || true; source .env.test 2>/dev/null || true; set +a; poetry run python scripts/migrate.py'

# Safe snapshot of current dev DB (requires sqlite3). Produces var/snapshots/app-<ts>.db
test-backup: dirs
	@bash -lc 'ts=$$(date +%Y%m%d-%H%M%S); if command -v sqlite3 >/dev/null 2>&1; then sqlite3 var/app.db ".backup \"var/snapshots/app-$$ts.db\"" && echo "[test-backup] created var/snapshots/app-$$ts.db"; else cp -f var/app.db var/snapshots/app-$$ts.db && echo "[test-backup] sqlite3 not found; used cp"; fi'

# Prepare test DB from current dev DB using sqlite3 .backup into var/test/app.db
test-db-from-prod: test-dirs
	@bash -lc 'if command -v sqlite3 >/dev/null 2>&1; then sqlite3 var/app.db ".backup \"var/test/app.db\"" && echo "[test-db] var/test/app.db ready"; else cp -f var/app.db var/test/app.db && echo "[test-db] sqlite3 not found; used cp"; fi'

# Run bot with .env + .env.test (test DB and DATA_DIR)
run-bot-test: test-dirs
	@bash -lc 'set -a; source .env 2>/dev/null || true; source .env.test 2>/dev/null || true; set +a; poetry run python -m app.bot.main'

# Remove only the test DB files
clean-test-db:
	rm -f var/test/app.db var/test/app.db-shm var/test/app.db-wal

# One-shot: prepare test dirs, migrate test DB
test-env: test-dirs test-migrate

lint:
	poetry run flake8 -j1 app

# Linting in CI-style: check-only and full repo scope
lint-ci:
	poetry run isort --check-only .
	poetry run black --check .
	poetry run flake8

fmt:
	poetry run black app tests && poetry run isort app tests

test:
	$(PY) pytest -q

doctor:
	bash scripts/doctor.sh

dirs:
	@mkdir -p $(DEV_DIRS)

migrate:
	$(PY) python scripts/migrate.py

seed:
	SEED_OWNER_TG_ID=dev_owner $(PY) python scripts/seed.py

clean-db:
	rm -f var/app.db var/app.db-shm var/app.db-wal

.PHONY: state-clean
state-clean:
	$(PY) python scripts/cleanup_state.py

.PHONY: ci
# Align with GitHub Actions: lint (check-only), migrate DB, tests, coverage summary
ci: lint-ci migrate test cov-summary


.PHONY: cov-summary
cov-summary:
	poetry run pytest -q --maxfail=1 --disable-warnings \
		--cov=app --cov-report=term-missing:skip-covered
