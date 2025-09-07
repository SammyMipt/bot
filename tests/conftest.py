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

    # Apply required migrations for tests
    migs = [
        "migrations/001_init.sql",
        "migrations/003_state_store_action_params.sql",
    ]
    for m in migs:
        p = pathlib.Path(m)
        if not p.exists():
            continue
        sql = p.read_text(encoding="utf-8")
        with conn.db() as c:
            c.executescript(sql)
            c.commit()

    # Note: additional migrations are applied on-demand in specific tests to avoid conflicts.

    return tmp_path


# ---------------- Async test support (no external plugin) -----------------


def pytest_configure(config):
    # Register asyncio marker to avoid unknown-mark warnings
    config.addinivalue_line("markers", "asyncio: mark test as async")


def pytest_pyfunc_call(pyfuncitem):
    """Execute async test functions via a local event loop.

    This removes the need for pytest-asyncio and silences related warnings.
    """
    import asyncio
    import inspect

    testfunction = pyfuncitem.obj
    if inspect.iscoroutinefunction(testfunction):
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            # Only pass fixtures that the function actually expects
            argnames = tuple(getattr(pyfuncitem, "_fixtureinfo").argnames or ())
            kwargs = {
                name: pyfuncitem.funcargs[name]
                for name in argnames
                if name in pyfuncitem.funcargs
            }
            loop.run_until_complete(testfunction(**kwargs))
        finally:
            try:
                loop.run_until_complete(asyncio.sleep(0))
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)
        return True
    return None
