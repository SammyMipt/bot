# Edu Bot — EPIC 0 Scaffold

## Быстрый старт
```bash
poetry install
make dev
make doctor
# добавьте TELEGRAM_TOKEN в .env
make run-bot
```

Команда `/start` должна отвечать «Привет! Это скелет бота.»

## Полезные команды
- `make fmt` — форматирование
- `make lint` — линтеры
- `make test` — тесты
- `make run-bot` — запустить бота

## Миграции и начальные данные

```bash
make migrate
make seed
sqlite3 var/app.db ".tables"
sqlite3 var/app.db "SELECT id, role, tg_id FROM users;"
