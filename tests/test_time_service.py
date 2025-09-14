import importlib
from datetime import datetime, timezone


def _apply_course_migrations():
    import app.db.conn as conn

    for m in [
        "migrations/004_course_weeks_schema.sql",
        "migrations/010_course_tz.sql",
    ]:
        with open(m, "r", encoding="utf-8") as f:
            sql = f.read()
        with conn.db() as c:
            c.executescript(sql)
            c.commit()


def test_get_course_tz_fallback_env(monkeypatch, db_tmpdir):
    monkeypatch.setenv("DEFAULT_COURSE_TZ", "Europe/Berlin")
    import app.services.common.time_service as ts

    importlib.reload(ts)
    assert ts.get_course_tz() == "Europe/Berlin"


def test_parse_deadline_and_format_in_course_tz(monkeypatch, db_tmpdir):
    # Use a fixed course tz via env fallback
    monkeypatch.setenv("DEFAULT_COURSE_TZ", "Europe/Moscow")
    import app.services.common.time_service as ts

    importlib.reload(ts)
    from zoneinfo import ZoneInfo

    # Date-only → 23:59 local
    utc_ts = ts.parse_deadline("2025-01-02")
    # Compute expected
    local = datetime(2025, 1, 2, 23, 59, tzinfo=ZoneInfo("Europe/Moscow"))
    assert utc_ts == int(local.astimezone(timezone.utc).timestamp())

    # Naive date-time → interpret as course tz
    utc_ts2 = ts.parse_deadline("2025-01-10 12:00")
    local2 = datetime(2025, 1, 10, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    assert utc_ts2 == int(local2.astimezone(timezone.utc).timestamp())

    # ISO with TZ offset overrides course tz
    utc_ts3 = ts.parse_deadline("2025-01-10T15:00:00+03:00")
    exp3 = int(datetime(2025, 1, 10, 12, 0, tzinfo=timezone.utc).timestamp())
    assert utc_ts3 == exp3

    # Formatting back to course tz
    s_date = ts.format_date(utc_ts)
    s_dt = ts.format_datetime(utc_ts2)
    assert s_date == "2025-01-02"
    assert s_dt.startswith("2025-01-10 12:00")


def test_local_to_utc_and_to_course_dt(monkeypatch, db_tmpdir):
    monkeypatch.setenv("DEFAULT_COURSE_TZ", "Europe/Kiev")
    import app.services.common.time_service as ts

    importlib.reload(ts)
    from zoneinfo import ZoneInfo

    # 2025-03-01 08:30 local -> UTC
    utc_ts = ts.local_to_utc_ts(2025, 3, 1, 8, 30)
    local = datetime(2025, 3, 1, 8, 30, tzinfo=ZoneInfo("Europe/Kiev"))
    assert utc_ts == int(local.astimezone(timezone.utc).timestamp())

    # Back to course dt
    dt_back = ts.to_course_dt(utc_ts)
    assert (dt_back.year, dt_back.month, dt_back.day, dt_back.hour, dt_back.minute) == (
        2025,
        3,
        1,
        8,
        30,
    )


def test_get_course_tz_from_db(monkeypatch, db_tmpdir):
    # Apply course schema and set tz in DB
    _apply_course_migrations()
    import app.db.conn as conn

    with conn.db() as c:
        c.execute(
            "INSERT OR IGNORE INTO course(id, name, created_at_utc, updated_at_utc, tz) "
            "VALUES(1,'Course', strftime('%s','now'), strftime('%s','now'), 'Asia/Tokyo')"
        )
        c.execute("UPDATE course SET tz='Asia/Tokyo' WHERE id=1")
        c.commit()

    import app.services.common.time_service as ts

    importlib.reload(ts)
    assert ts.get_course_tz() == "Asia/Tokyo"
