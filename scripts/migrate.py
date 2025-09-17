#!/usr/bin/env python
import os
import sqlite3
import sys
from pathlib import Path

try:
    # Prefer project config/env if available
    from app.core.config import cfg  # type: ignore

    SQLITE_PATH = Path(cfg.sqlite_path)
except Exception:
    # Fallback to env or default
    SQLITE_PATH = Path(os.getenv("SQLITE_PATH", "./var/app.db"))

MIGRATIONS_DIR = Path("./migrations")


def main():
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("CREATE TABLE IF NOT EXISTS __migrations__(name TEXT PRIMARY KEY)")
    applied = {row[0] for row in conn.execute("SELECT name FROM __migrations__")}

    for sql in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql.name in applied:
            continue
        print(f"[migrate] applying {sql.name}")
        conn.executescript(sql.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO __migrations__(name) VALUES (?)", (sql.name,))
        conn.commit()

    conn.close()
    print("[migrate] done")


if __name__ == "__main__":
    sys.exit(main())
