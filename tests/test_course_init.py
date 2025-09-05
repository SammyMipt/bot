import io
import time
from datetime import datetime, timezone

import pytest

from app.core.course_init import WeekRow, apply_course_init, parse_weeks_csv
from app.db.conn import db


def _mig_004():
    with open("migrations/004_course_weeks_schema.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    with db() as conn:
        conn.executescript(sql)
        conn.commit()


def _csv_text(rows):
    out = io.StringIO()
    out.write("week_id,topic,description,deadline\n")
    for r in rows:
        out.write(
            f"{r.get('week_id', '')},{r.get('topic', '')},{r.get('description', '')},{r.get('deadline', '')}\n"
        )
    return out.getvalue().encode("utf-8")


def test_parse_headers_mismatch_returns_import_format():
    bad = "w,topic,description,deadline\n1,A,B,2025-01-01\n".encode("utf-8")
    res = parse_weeks_csv(bad)
    assert res.rows == []
    assert any(e == "E_IMPORT_FORMAT" for e in res.errors)


def test_parse_valid_date_only_sets_2359_utc():
    csvb = _csv_text(
        [
            {
                "week_id": "W01",
                "topic": "Intro",
                "description": "",
                "deadline": "2025-01-02",
            }
        ]
    )
    res = parse_weeks_csv(csvb)
    assert res.errors == []
    assert len(res.rows) == 1
    dl = res.rows[0].deadline_ts_utc
    assert isinstance(dl, int)
    exp = int(datetime(2025, 1, 2, 23, 59, tzinfo=timezone.utc).timestamp())
    assert dl == exp


def test_parse_invalid_deadline_marks_import_format_deadline():
    csvb = _csv_text(
        [
            {
                "week_id": "1",
                "topic": "T",
                "description": "",
                "deadline": "2025/01/02",  # invalid
            }
        ]
    )
    res = parse_weeks_csv(csvb)
    assert any("E_IMPORT_FORMAT" in e and "deadline" in e for e in res.errors)


def test_parse_duplicate_and_gap_errors():
    # duplicate (W01 and 1 refer to week 1), and gap (no week 2 but week 3 present)
    csvb = _csv_text(
        [
            {"week_id": "W01", "topic": "t1", "description": "", "deadline": ""},
            {"week_id": "1", "topic": "dup", "description": "", "deadline": ""},
            {"week_id": "3", "topic": "t3", "description": "", "deadline": ""},
        ]
    )
    res = parse_weeks_csv(csvb)
    assert any("E_WEEK_DUPLICATE" in e for e in res.errors)
    assert any("E_WEEK_SEQUENCE_GAP" in e for e in res.errors)


@pytest.mark.usefixtures("db_tmpdir")
def test_apply_course_init_inserts_updates_and_deletes_extras():
    _mig_004()
    now = int(time.time())
    with db() as conn:
        # precreate weeks 1 and 99 to check update and deletion
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(1, 'Old', ?)",
            (now,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(99, 'Extra', ?)",
            (now,),
        )
        conn.commit()

    rows = [
        WeekRow(week_no=1, topic="Intro", description="Desc1", deadline_ts_utc=None),
        WeekRow(
            week_no=2,
            topic="Vectors",
            description="Desc2",
            deadline_ts_utc=int(
                datetime(2025, 1, 10, 12, 0, tzinfo=timezone.utc).timestamp()
            ),
        ),
    ]
    apply_course_init(rows)

    with db() as conn:
        # week 99 should be deleted
        r = conn.execute("SELECT COUNT(1) FROM weeks WHERE week_no=99").fetchone()
        assert r[0] == 0

        # weeks 1 and 2 should exist with updated fields
        w1 = conn.execute(
            "SELECT title, topic, description, deadline_ts_utc FROM weeks WHERE week_no=1"
        ).fetchone()
        assert w1[0] == "Intro" and w1[1] == "Intro" and w1[2] == "Desc1"
        assert w1[3] is None

        w2 = conn.execute(
            "SELECT title, topic, description, deadline_ts_utc FROM weeks WHERE week_no=2"
        ).fetchone()
        assert w2[0] == "Vectors" and w2[1] == "Vectors" and w2[2] == "Desc2"
        assert isinstance(w2[3], int) and w2[3] > 0
