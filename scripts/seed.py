#!/usr/bin/env python
import os
import sqlite3
import time
from pathlib import Path

DB = Path("./var/app.db")


def now():
    return int(time.time())


def main():
    tg_owner = os.getenv("SEED_OWNER_TG_ID", "owner_dev")
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys=ON;")

    conn.execute(
        """
        INSERT OR IGNORE INTO users(tg_id, role, name, created_at_utc, updated_at_utc)
        VALUES(?, 'owner', 'Owner Dev', ?, ?)
    """,
        (tg_owner, now(), now()),
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc)
        VALUES(1, 'Week 1', ?)
    """,
        (now(),),
    )

    conn.commit()
    conn.close()
    print("[seed] done")


if __name__ == "__main__":
    main()
