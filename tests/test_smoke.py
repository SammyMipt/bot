def test_truth():
    assert True


def test_migrate_creates_tables():
    import pathlib
    import sqlite3
    import subprocess

    db = pathlib.Path("var/app.db")
    subprocess.run(["python", "scripts/migrate.py"], check=True)
    conn = sqlite3.connect(db)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "users" in tables
