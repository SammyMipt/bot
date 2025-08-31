from dataclasses import dataclass
from typing import Dict, List, Optional

from app.db.conn import db  # твой helper


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
    week_no: Optional[int] = None  # для удобства отдаём номер недели


def list_materials_by_week(week_no: int) -> List[Material]:
    # materials -> assignments (по assignment_id) -> filter a.week_no
    q = """
    SELECT m.id, m.assignment_id, m.path, m.sha256, m.size_bytes, m.mime, m.uploaded_by, m.created_at_utc, a.week_no
    FROM materials m
    JOIN assignments a ON a.id = m.assignment_id
    WHERE a.week_no = ?
    ORDER BY m.id ASC
    """
    with db() as conn:
        rows = conn.execute(q, (week_no,)).fetchall()
    return [Material(*row) for row in rows]


def insert_material_file(
    assignment_id: int,
    uploaded_by: int,
    path: str,
    sha256: str,
    size_bytes: int,
    mime: Optional[str] = None,
) -> int:
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO materials(assignment_id, path, sha256, size_bytes, mime, uploaded_by, created_at_utc)
            VALUES(?,?,?,?,?, ?, strftime('%s','now'))
            """,
            (assignment_id, path, sha256, size_bytes, mime, uploaded_by),
        )
        return cur.lastrowid


def list_submissions_by_student(student_id: int) -> List[Dict]:
    q = """
    SELECT id, assignment_id, status, created_at_utc, size_bytes, original_name
    FROM submissions
    WHERE student_id = ?
    ORDER BY id DESC
    """
    with db() as conn:
        cur = conn.execute(q, (student_id,))
        return [
            dict(
                id=r[0],
                assignment_id=r[1],
                status=r[2],
                created_at_utc=r[3],
                size_bytes=r[4],
                name=r[5],
            )
            for r in cur.fetchall()
        ]
