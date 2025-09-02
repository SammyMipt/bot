from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.db.conn import db

# Error codes
E_CSV_BAD_HEADERS = "E_CSV_BAD_HEADERS"
E_FIELD_REQUIRED = "E_FIELD_REQUIRED"
E_TEF_INVALID = "E_TEF_INVALID"
E_CAPACITY_INVALID = "E_CAPACITY_INVALID"
E_EMAIL_INVALID = "E_EMAIL_INVALID"
E_GROUP_INVALID = "E_GROUP_INVALID"
E_DUPLICATE_USER = "E_DUPLICATE_USER"


TEACHER_HEADERS = ["surname", "name", "patronymic", "email", "tef", "capacity"]
STUDENT_HEADERS = ["surname", "name", "patronymic", "email", "group_name"]


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class ImportResult:
    created: int
    updated: int
    errors: List[Tuple[int, str, str, str]]  # row_index, field, error_code, message

    def to_error_csv(self) -> bytes:
        if not self.errors:
            return b""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["row_index", "field", "error_code", "message"])
        for row_index, field, code, msg in self.errors:
            w.writerow([row_index, field, code, msg])
        return buf.getvalue().encode("utf-8")


def _full_name(surname: str, name: str, patronymic: str) -> str:
    parts = [surname.strip(), name.strip()]
    p = patronymic.strip()
    if p:
        parts.append(p)
    return " ".join(parts)


def _parse_csv(
    content: bytes, expected_headers: List[str]
) -> Tuple[List[Dict[str, str]], List[Tuple[int, str, str, str]]]:
    errors: List[Tuple[int, str, str, str]] = []
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        headers = next(reader)
    except StopIteration:
        return [], [(0, "-", E_CSV_BAD_HEADERS, "empty file")]  # no rows at all
    if headers != expected_headers:
        return [], [
            (
                0,
                "-",
                E_CSV_BAD_HEADERS,
                f"expected headers: {','.join(expected_headers)}",
            )
        ]
    rows: List[Dict[str, str]] = []
    for i, row in enumerate(reader, start=1):  # 1-based data rows
        # pad or trim to expected length to avoid IndexError
        if len(row) < len(expected_headers):
            row = row + [""] * (len(expected_headers) - len(row))
        elif len(row) > len(expected_headers):
            row = row[: len(expected_headers)]
        rows.append(
            {h: (row[idx] or "").strip() for idx, h in enumerate(expected_headers)}
        )
    return rows, errors


def _find_user_by_email_or_name(
    conn, role: str, email: Optional[str], name: str
) -> Optional[int]:
    if email:
        r = conn.execute(
            "SELECT id FROM users WHERE role=? AND email=? LIMIT 1",
            (role, email),
        ).fetchone()
        if r:
            return int(r[0])
    # fallback by name (email absent or not found)
    r = conn.execute(
        "SELECT id FROM users WHERE role=? AND LOWER(COALESCE(name,''))=LOWER(?) ORDER BY id ASC LIMIT 2",
        (role, name),
    ).fetchall()
    if not r:
        return None
    if len(r) > 1:
        # ambiguous — treat as duplicate
        return -1
    return int(r[0][0])


def import_teachers_csv(content: bytes) -> ImportResult:
    data_rows, errors = _parse_csv(content, TEACHER_HEADERS)
    if errors:
        return ImportResult(created=0, updated=0, errors=errors)

    created = 0
    updated = 0
    seen_keys = set()  # detect duplicates inside the CSV

    with db() as conn:
        for idx, row in enumerate(data_rows, start=1):
            surname = row["surname"].strip()
            name = row["name"].strip()
            patronymic = row["patronymic"].strip()
            email = row["email"].strip()
            tef_raw = row["tef"].strip()
            cap_raw = row["capacity"].strip()

            # required fields
            if not surname:
                errors.append((idx, "surname", E_FIELD_REQUIRED, "surname is required"))
                continue
            if not name:
                errors.append((idx, "name", E_FIELD_REQUIRED, "name is required"))
                continue

            full_name = _full_name(surname, name, patronymic)

            # email validate if provided
            if email and not _EMAIL_RE.match(email):
                errors.append((idx, "email", E_EMAIL_INVALID, "invalid email"))
                continue

            # tef and capacity must be positive integers
            try:
                tef = int(tef_raw)
                if tef <= 0:
                    raise ValueError
            except Exception:
                errors.append((idx, "tef", E_TEF_INVALID, "tef must be > 0"))
                continue
            try:
                capacity = int(cap_raw)
                if capacity <= 0:
                    raise ValueError
            except Exception:
                errors.append(
                    (idx, "capacity", E_CAPACITY_INVALID, "capacity must be > 0")
                )
                continue

            # duplicate key within CSV: prefer email if present else full name
            key = (email.lower() if email else None) or f"name:{full_name.lower()}"
            if key in seen_keys:
                errors.append((idx, "-", E_DUPLICATE_USER, "duplicate row in CSV"))
                continue
            seen_keys.add(key)

            # upsert in DB
            found = _find_user_by_email_or_name(
                conn, role="teacher", email=email or None, name=full_name
            )
            if found == -1:
                errors.append(
                    (idx, "-", E_DUPLICATE_USER, "ambiguous match in DB by name")
                )
                continue
            if found:
                conn.execute(
                    (
                        "UPDATE users SET name=?, email=?, tef=?, capacity=?, is_active=1, "
                        "updated_at_utc=strftime('%s','now') WHERE id=?"
                    ),
                    (full_name, email or None, tef, capacity, found),
                )
                updated += 1
            else:
                conn.execute(
                    (
                        "INSERT INTO users("
                        "tg_id, role, name, email, tef, capacity, is_active, "
                        "created_at_utc, updated_at_utc"
                        ") VALUES("
                        "NULL, 'teacher', ?, ?, ?, ?, 1, strftime('%s','now'), strftime('%s','now')"
                        ")"
                    ),
                    (full_name, email or None, tef, capacity),
                )
                created += 1

    return ImportResult(created=created, updated=updated, errors=errors)


def import_students_csv(content: bytes) -> ImportResult:
    data_rows, errors = _parse_csv(content, STUDENT_HEADERS)
    if errors:
        return ImportResult(created=0, updated=0, errors=errors)

    created = 0
    updated = 0
    seen_keys = set()

    with db() as conn:
        for idx, row in enumerate(data_rows, start=1):
            surname = row["surname"].strip()
            name = row["name"].strip()
            patronymic = row["patronymic"].strip()
            email = row["email"].strip()
            group_name = row["group_name"].strip()

            # required fields
            if not surname:
                errors.append((idx, "surname", E_FIELD_REQUIRED, "surname is required"))
                continue
            if not name:
                errors.append((idx, "name", E_FIELD_REQUIRED, "name is required"))
                continue

            full_name = _full_name(surname, name, patronymic)

            # email validate if provided
            if email and not _EMAIL_RE.match(email):
                errors.append((idx, "email", E_EMAIL_INVALID, "invalid email"))
                continue

            # group validation (if provided)
            if group_name and len(group_name) > 128:
                errors.append((idx, "group_name", E_GROUP_INVALID, "too long"))
                continue

            key = (email.lower() if email else None) or f"name:{full_name.lower()}"
            if key in seen_keys:
                errors.append((idx, "-", E_DUPLICATE_USER, "duplicate row in CSV"))
                continue
            seen_keys.add(key)

            found = _find_user_by_email_or_name(
                conn, role="student", email=email or None, name=full_name
            )
            if found == -1:
                errors.append(
                    (idx, "-", E_DUPLICATE_USER, "ambiguous match in DB by name")
                )
                continue
            if found:
                conn.execute(
                    (
                        "UPDATE users SET name=?, email=?, group_name=?, is_active=1, "
                        "updated_at_utc=strftime('%s','now') WHERE id=?"
                    ),
                    (full_name, email or None, group_name or None, found),
                )
                updated += 1
            else:
                conn.execute(
                    (
                        "INSERT INTO users("
                        "tg_id, role, name, email, group_name, is_active, "
                        "created_at_utc, updated_at_utc"
                        ") VALUES("
                        "NULL, 'student', ?, ?, ?, 1, strftime('%s','now'), strftime('%s','now')"
                        ")"
                    ),
                    (full_name, email or None, group_name or None),
                )
                created += 1

    return ImportResult(created=created, updated=updated, errors=errors)


def get_users_summary() -> Dict[str, int]:
    with db() as conn:
        t_total = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='teacher'"
        ).fetchone()[0]
        t_no_tg = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='teacher' AND tg_id IS NULL"
        ).fetchone()[0]
        s_total = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='student'"
        ).fetchone()[0]
        s_no_tg = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='student' AND tg_id IS NULL"
        ).fetchone()[0]
    return {
        "teachers_total": int(t_total),
        "teachers_no_tg": int(t_no_tg),
        "students_total": int(s_total),
        "students_no_tg": int(s_no_tg),
    }


def get_templates() -> Dict[str, bytes]:
    buf_t = io.StringIO()
    wt = csv.writer(buf_t)
    wt.writerow(TEACHER_HEADERS)
    # example row is omitted; only headers required

    buf_s = io.StringIO()
    ws = csv.writer(buf_s)
    ws.writerow(STUDENT_HEADERS)

    return {
        "teachers.csv": buf_t.getvalue().encode("utf-8"),
        "students.csv": buf_s.getvalue().encode("utf-8"),
    }
