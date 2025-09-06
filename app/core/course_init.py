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
    from datetime import datetime, timezone

    # Date-only → default to 23:59 (UTC)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        d = datetime.strptime(v, "%Y-%m-%d")
        dt = d.replace(hour=23, minute=59, second=0, microsecond=0, tzinfo=timezone.utc)
        return int(dt.timestamp())

    for fmt in (None, "%Y-%m-%d %H:%M"):
        try:
            if fmt is None:
                dt = datetime.fromisoformat(v)
                # Приводим к UTC: если наивный — считаем UTC; если с TZ — переводим в UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
            else:
                dt = datetime.strptime(v, fmt).replace(tzinfo=timezone.utc)
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
        # Синхронизация с реестром ошибок: формат импорта
        return ParseResult(rows=[], errors=["E_IMPORT_FORMAT"])
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
            # Accept numeric or W-prefixed codes (e.g., W01)
            import re

            m = re.fullmatch(r"[Ww]?0*(\d+)", week_raw)
            if not m:
                raise ValueError("invalid week_id format")
            week_no = int(m.group(1))
            if week_no <= 0:
                raise ValueError("invalid week_id")
            deadline_ts = None
            if deadline_raw:
                try:
                    deadline_ts = _parse_deadline(deadline_raw)
                except Exception:
                    # Синхронизация формата ошибок по реестру
                    errors.append(f"{idx}:E_IMPORT_FORMAT:deadline")
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
    # Post-validate uniqueness and continuity per spec
    if rows:
        ids = [r.week_no for r in rows]
        if len(set(ids)) != len(ids):
            errors.append("E_WEEK_DUPLICATE")
        mn, mx = min(ids), max(ids)
        if mn != 1 or (mx - mn + 1) != len(set(ids)):
            errors.append("E_WEEK_SEQUENCE_GAP")
    return ParseResult(rows=rows, errors=errors)


def apply_course_init(rows: List[WeekRow]) -> None:
    now = int(time.time())
    new_week_nos = {r.week_no for r in rows}
    with db() as conn:
        # Переинициализация: удаляем недели, отсутствующие в новом CSV
        if new_week_nos:
            existing_rows = conn.execute("SELECT week_no FROM weeks").fetchall()
            existing_nos = {int(x[0]) for x in existing_rows}
            to_delete = sorted(existing_nos - new_week_nos)
            for w in to_delete:
                conn.execute("DELETE FROM weeks WHERE week_no=?", (w,))
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
