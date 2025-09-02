import importlib
import pathlib
import sys

import pytest

# Ensure project root on sys.path for `import app`
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def db_tmpdir(tmp_path, monkeypatch):
    data_dir = tmp_path / "var"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "app.db"

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("SQLITE_PATH", str(db_path))
    monkeypatch.setenv("APP_VAR_DIR", str(data_dir))

    import app.core.config as config

    importlib.reload(config)
    import app.db.conn as conn

    importlib.reload(conn)
    import app.core.files as files

    importlib.reload(files)

    mig_path = pathlib.Path("migrations/001_init.sql")
    sql = mig_path.read_text(encoding="utf-8")
    with conn.db() as c:
        c.executescript(sql)
        c.commit()

    mig_path2 = pathlib.Path("migrations/003_state_store_action_params.sql")
    if mig_path2.exists():
        sql2 = mig_path2.read_text(encoding="utf-8")
        with conn.db() as c:
            c.executescript(sql2)
            c.commit()

    return tmp_path
