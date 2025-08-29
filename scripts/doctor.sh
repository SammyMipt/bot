#!/usr/bin/env bash
set -euo pipefail

echo "[doctor] checking python & poetry"
python --version
poetry --version

echo "[doctor] checking env"
if [ ! -f .env ]; then
  echo "copying .env.example -> .env"; cp .env.example .env
fi

source .env || true

mkdir -p var var/materials var/submissions var/exports var/tmp var/logs

echo "[doctor] writable dirs"
for d in var var/materials var/submissions var/exports var/tmp var/logs; do
  test -w "$d" || (echo "dir not writable: $d" && exit 1)
  echo "ok: $d"
done

echo "[doctor] disk space"
df -h . | tail -n +2

echo "[doctor] checking pre-commit"
poetry run pre-commit --version || echo "pre-commit is not available (will be installed by poetry)"

echo "[doctor] sqlite3 version"
sqlite3 --version || echo "sqlite3 not found (optional for manual inspection)"

echo "[doctor] done"
