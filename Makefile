.PHONY: init dev run-bot lint fmt test doctor dirs migrate seed clean-db fix-eol lint-ci cov-summary

PY=poetry run
DEV_DIRS=var var/materials var/submissions var/exports var/tmp var/logs

init: dirs
	poetry install

dev: init
	@echo "Dev env ready"

run-bot:
	$(PY) python -m app.bot.main

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
