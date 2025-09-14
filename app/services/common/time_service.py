from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from app.db.conn import db

DEFAULT_TZ_ENV = "DEFAULT_COURSE_TZ"


def _env_default_tz() -> str:
    return os.getenv(DEFAULT_TZ_ENV, os.getenv("TZ", "UTC")) or "UTC"


def utc_now_ts() -> int:
    return int(time.time())


def get_course_tz() -> str:
    """Return course timezone (IANA). Fallback chain: DB -> env DEFAULT_COURSE_TZ -> 'UTC'."""
    # Try DB (course.id=1)
    try:
        with db() as conn:
            row = conn.execute("SELECT tz FROM course WHERE id=1").fetchone()
            if row and row[0]:
                return str(row[0])
    except Exception:
        pass
    return _env_default_tz()


def _zone(tz_name: str) -> ZoneInfo:
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo is required for TimeService")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        # Fallback strictly to UTC on invalid tz
        return ZoneInfo("UTC")


def to_course_dt(utc_ts: int, course_tz: Optional[str] = None) -> datetime:
    tz = _zone(course_tz or get_course_tz())
    return datetime.fromtimestamp(int(utc_ts), tz=timezone.utc).astimezone(tz)


def format_date(utc_ts: int, course_tz: Optional[str] = None) -> str:
    return to_course_dt(utc_ts, course_tz).strftime("%Y-%m-%d")


def format_datetime(utc_ts: int, course_tz: Optional[str] = None) -> str:
    return to_course_dt(utc_ts, course_tz).strftime("%Y-%m-%d %H:%M")


def course_today(course_tz: Optional[str] = None) -> datetime:
    tz = _zone(course_tz or get_course_tz())
    now_local = datetime.now(tz)
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0)


def local_to_utc_ts(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    *,
    course_tz: Optional[str] = None,
) -> int:
    tz = _zone(course_tz or get_course_tz())
    local = datetime(year, month, day, hour, minute, tzinfo=tz)
    return int(local.astimezone(timezone.utc).timestamp())


def parse_deadline(dt_str: str, course_tz: Optional[str] = None) -> int:
    """Parse deadline string relative to course TZ and return UTC epoch seconds.

    Rules:
      - 'YYYY-MM-DD' => 23:59:00 at course_tz
      - 'YYYY-MM-DD HH:MM' => that local time at course_tz
      - ISO with TZ (e.g., '2025-01-02T12:00:00+03:00') => converted to UTC
      - ISO without TZ => interpret as course_tz
    Raises ValueError on invalid format.
    """
    v = (dt_str or "").strip()
    if not v:
        raise ValueError("empty datetime string")
    tzname = course_tz or get_course_tz()
    tz = _zone(tzname)

    # Date-only
    import re

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        y, m, d = map(int, v.split("-"))
        local = datetime(y, m, d, 23, 59, 0, tzinfo=tz)
        return int(local.astimezone(timezone.utc).timestamp())

    # Try strict 'YYYY-MM-DD HH:MM'
    try:
        dt = datetime.strptime(v, "%Y-%m-%d %H:%M")
        dt = dt.replace(tzinfo=tz)
        return int(dt.astimezone(timezone.utc).timestamp())
    except Exception:
        pass

    # Try ISO parser
    try:
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return int(dt.astimezone(timezone.utc).timestamp())
    except Exception:
        pass

    raise ValueError("invalid datetime format")


def format_dual_tz(utc_ts: int, course_tz: str, user_tz: str) -> str:
    """Return 'YYYY-MM-DD HH:MM (course_tz) (у вас сейчас: HH:MM)'."""
    ct = _zone(course_tz)
    ut = _zone(user_tz)
    base = datetime.fromtimestamp(int(utc_ts), tz=timezone.utc)
    cdt = base.astimezone(ct)
    udt = base.astimezone(ut)
    return f"{cdt.strftime('%Y-%m-%d %H:%M')} ({course_tz}) (у вас сейчас: {udt.strftime('%H:%M')})"


@dataclass
class TimeService:
    """Convenience facade if DI is preferred."""

    course_tz: Optional[str] = None

    def tz(self) -> str:
        return self.course_tz or get_course_tz()

    def now_utc(self) -> int:
        return utc_now_ts()

    def parse_deadline(self, s: str) -> int:
        return parse_deadline(s, self.tz())

    def to_course_dt(self, utc_ts: int) -> datetime:
        return to_course_dt(utc_ts, self.tz())

    def format_date(self, utc_ts: int) -> str:
        return format_date(utc_ts, self.tz())

    def format_datetime(self, utc_ts: int) -> str:
        return format_datetime(utc_ts, self.tz())

    def course_today(self) -> datetime:
        return course_today(self.tz())

    def local_to_utc_ts(self, y: int, m: int, d: int, hh: int, mm: int) -> int:
        return local_to_utc_ts(y, m, d, hh, mm, course_tz=self.tz())
