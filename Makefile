    .PHONY: init dev run-bot lint fmt test doctor dirs

    PY=poetry run

    DEV_DIRS=var var/materials var/submissions var/exports var/tmp var/logs

    init: dirs
	poetry install

    dev: init
	@echo "Dev env ready"

    run-bot:
	$(PY) python -m app.bot.main

    lint:
	poetry run flake8 app

    fmt:
	poetry run black app tests && poetry run isort app tests

    test:
	$(PY) pytest -q

    doctor:
	bash scripts/doctor.sh

    dirs:
	@mkdir -p $(DEV_DIRS)
