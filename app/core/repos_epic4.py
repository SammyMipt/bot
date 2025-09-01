from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# В проекте подключение к БД — так:
from app.db.conn import db

# ---------- MODELS ----------


@dataclass
class Material:
    id: int
    assignment_id: Optional[int]
    path: str
    sha256: str
    size_bytes: int
    mime: Optional[str]
    uploaded_by: int
    created_at_utc: int
    week_no: Optional[int] = None
    visibility: Optional[str] = None


# ---------- MATERIALS (недельные, с видимостью) ----------


def list_materials_by_week(week_no: int, audience: str = "student") -> List[Material]:
    """
    Вернёт материалы по неделе с фильтром видимости.
    audience: 'student' | 'teacher'
    - student видит только visibility='public'
    - teacher/owner видит всё
    """
    with db() as conn:
        if audience == "teacher":
            rows = conn.execute(
                """
                SELECT m.id, m.assignment_id, m.path, m.sha256, m.size_bytes, m.mime,
                       m.uploaded_by, m.created_at_utc, m.week_no, m.visibility
                FROM materials m
                LEFT JOIN assignments a ON a.id = m.assignment_id
                WHERE (m.week_no = ? OR (a.week_no = ?))
                ORDER BY m.id ASC
                """,
                (week_no, week_no),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT m.id, m.assignment_id, m.path, m.sha256, m.size_bytes, m.mime,
                       m.uploaded_by, m.created_at_utc, m.week_no, m.visibility
                FROM materials m
                LEFT JOIN assignments a ON a.id = m.assignment_id
                WHERE (m.week_no = ? OR (a.week_no = ?))
                  AND m.visibility = 'public'
                ORDER BY m.id ASC
                """,
                (week_no, week_no),
            ).fetchall()
    return [Material(*row) for row in rows]


def insert_week_material_file(
    week_no: int,
    uploaded_by: int,
    path: str,
    sha256: str,
    size_bytes: int,
    mime: Optional[str],
    visibility: str = "public",
) -> int:
    assert visibility in ("public", "teacher_only")
    with db() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO materials(
                  week_no, assignment_id, path, sha256, size_bytes,
                  mime, visibility, uploaded_by, created_at_utc
                )
                VALUES(?, NULL, ?, ?, ?, ?, ?, ?, strftime('%s','now'))
                """,
                (week_no, path, sha256, size_bytes, mime, visibility, uploaded_by),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # Duplicate content (sha256 + size) — treat as already exists
            return -1


# ---------- WEEK SUBMISSIONS (недельные, многофайловые) ----------


def get_or_create_week_submission(student_id: int, week_no: int) -> int:
    """Вернёт id существующей сдачи студента за неделю или создаст новую (status='submitted')."""
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM submissions WHERE student_id=? AND week_no=? ORDER BY id DESC LIMIT 1",
            (student_id, week_no),
        ).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(
            (
                "INSERT INTO submissions(week_no, student_id, status, created_at_utc) "
                "VALUES(?, ?, 'submitted', strftime('%s','now'))"
            ),
            (week_no, student_id),
        )
        return cur.lastrowid


def add_submission_file(
    submission_id: int,
    sha256: str,
    size_bytes: int,
    path: str,
    mime: Optional[str],
) -> int:
    """
    Добавит файл в недельную сдачу.
    Возвращает file_id, а при дубликате (тот же sha256+size, не удалённый) — существующий id.
    """
    with db() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO week_submission_files(submission_id, sha256, size_bytes, path, mime, created_at_utc)
                VALUES(?,?,?,?,?, strftime('%s','now'))
                """,
                (submission_id, sha256, size_bytes, path, mime),
            )
            return cur.lastrowid
        except Exception:
            # Может сработать UNIQUE (submission_id, sha256, size_bytes) WHERE deleted_at_utc IS NULL
            row = conn.execute(
                """
                SELECT id FROM week_submission_files
                WHERE submission_id=? AND sha256=? AND size_bytes=? AND deleted_at_utc IS NULL
                """,
                (submission_id, sha256, size_bytes),
            ).fetchone()
            return int(row[0]) if row else -1


def list_submission_files(student_id: int, week_no: int) -> List[Dict]:
    """Файлы сдачи студента за неделю (только не удалённые)."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.sha256, f.size_bytes, f.path, f.mime, f.created_at_utc
            FROM submissions s
            JOIN week_submission_files f ON f.submission_id = s.id
            WHERE s.student_id=? AND s.week_no=? AND f.deleted_at_utc IS NULL
            ORDER BY f.id ASC
            """,
            (student_id, week_no),
        ).fetchall()
    return [
        {
            "id": r[0],
            "sha256": r[1],
            "size_bytes": r[2],
            "path": r[3],
            "mime": r[4],
            "created_at_utc": r[5],
        }
        for r in rows
    ]


def soft_delete_submission_file(file_id: int, student_id: int) -> bool:
    """Мягкое удаление файла сдачи (проверяется, что файл принадлежит сдаче данного студента)."""
    with db() as conn:
        row = conn.execute(
            """
            SELECT f.id
            FROM week_submission_files f
            JOIN submissions s ON s.id = f.submission_id
            WHERE f.id=? AND s.student_id=? AND f.deleted_at_utc IS NULL
            """,
            (file_id, student_id),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE week_submission_files SET deleted_at_utc=strftime('%s','now') WHERE id=?",
            (file_id,),
        )
        return True


def list_student_weeks(student_id: int, limit: int = 20) -> List[Tuple[int, int]]:
    """
    Возвращает список (week_no, files_count) по неделям, где у студента есть сдачи.
    """
    with db() as conn:
        rows = conn.execute(
            """
            SELECT s.week_no, COUNT(f.id) AS cnt
            FROM submissions s
            LEFT JOIN week_submission_files f ON f.submission_id=s.id AND f.deleted_at_utc IS NULL
            WHERE s.student_id=? AND s.week_no IS NOT NULL
            GROUP BY s.week_no
            ORDER BY s.week_no DESC
            LIMIT ?
            """,
            (student_id, limit),
        ).fetchall()
    return [(int(r[0]), int(r[1])) for r in rows]


# ---------- WEEKS (для клавиатур) ----------


def list_weeks(limit: int = 50) -> List[int]:
    """Возвращает список номеров недель из таблицы weeks в порядке возрастания."""
    with db() as conn:
        rows = conn.execute(
            "SELECT week_no FROM weeks ORDER BY week_no ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [int(r[0]) for r in rows]
