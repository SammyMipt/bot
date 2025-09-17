import pytest

pytestmark = pytest.mark.usefixtures("db_tmpdir")


def test_truth():
    assert True


def test_migrate_creates_tables():
    import sqlite3
    import subprocess

    # Use cfg.sqlite_path to ensure subprocess writes to tmp DB
    from app.core.config import cfg

    subprocess.run(["python", "scripts/migrate.py"], check=True)
    conn = sqlite3.connect(cfg.sqlite_path)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "users" in tables
