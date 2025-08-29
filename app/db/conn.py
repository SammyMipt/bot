import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.core.config import cfg

Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(cfg.sqlite_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


_CONN = _connect()


@contextmanager
def db() -> sqlite3.Connection:
    try:
        yield _CONN
    finally:
        pass
