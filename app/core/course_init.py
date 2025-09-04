from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from typing import List, Optional

from app.db.conn import db

EXPECTED_HEADERS = ["week_id", "topic", "description", "deadline"]


@dataclass
class WeekRow:
    week_no: int
    topic: str
    description: str
    deadline_ts_utc: Optional[int]


@dataclass
class ParseResult:
    rows: List[WeekRow]
    errors: List[str]


def _parse_deadline(value: str) -> Optional[int]:
    v = (value or "").strip()
    if not v:
        return None
    import re
    from datetime import datetime

    # Date-only → default to 23:59 (UTC)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        d = datetime.strptime(v, "%Y-%m-%d")
        dt = d.replace(hour=23, minute=59, second=0, microsecond=0)
        return int(dt.timestamp())

    for fmt in (None, "%Y-%m-%d %H:%M"):
        try:
            if fmt is None:
                dt = datetime.fromisoformat(v)
            else:
                dt = datetime.strptime(v, fmt)
            return int(dt.timestamp())
        except Exception:
            continue
    raise ValueError("invalid deadline format")


def parse_weeks_csv(content: bytes) -> ParseResult:
    text = content.decode("utf-8", errors="replace")
    f = io.StringIO(text)
    reader = csv.DictReader(f)
    headers = [h.strip() for h in (reader.fieldnames or [])]
    if headers != EXPECTED_HEADERS:
        return ParseResult(rows=[], errors=["E_FORMAT_COLUMNS"])
    rows: List[WeekRow] = []
    errors: List[str] = []
    for idx, row in enumerate(reader, start=2):
        try:
            week_raw = (row.get("week_id") or "").strip()
            topic = (row.get("topic") or "").strip()
            description = (row.get("description") or "").strip()
            deadline_raw = (row.get("deadline") or "").strip()
            if not week_raw:
                raise ValueError("missing week_id")
            week_no = int(week_raw)
            if week_no <= 0:
                raise ValueError("invalid week_id")
            deadline_ts = None
            if deadline_raw:
                try:
                    deadline_ts = _parse_deadline(deadline_raw)
                except Exception:
                    errors.append(f"{idx}:E_DEADLINE_INVALID")
                    continue
            rows.append(
                WeekRow(
                    week_no=week_no,
                    topic=topic,
                    description=description,
                    deadline_ts_utc=deadline_ts,
                )
            )
        except Exception as e:
            errors.append(f"{idx}:E_ROW_INVALID:{e}")
    return ParseResult(rows=rows, errors=errors)


def apply_course_init(rows: List[WeekRow]) -> None:
    now = int(time.time())
    with db() as conn:
        for r in rows:
            conn.execute(
                "INSERT OR IGNORE INTO weeks(week_no, title, created_at_utc) VALUES(?,?,?)",
                (r.week_no, r.topic, now),
            )
            conn.execute(
                "UPDATE weeks SET "
                "  title=COALESCE(?, title), "
                "  topic=COALESCE(?, topic), "
                "  description=COALESCE(?, description), "
                "  deadline_ts_utc=COALESCE(?, deadline_ts_utc) "
                "WHERE week_no=?",
                (
                    r.topic or None,
                    r.topic or None,
                    r.description or None,
                    r.deadline_ts_utc,
                    r.week_no,
                ),
            )
            # assignments removed; weeks carry metadata directly
        conn.commit()
