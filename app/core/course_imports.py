from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Literal, Optional

from app.core.repos_epic4 import set_week_grade
from app.db.conn import db

AssignmentStatus = Literal["new", "update", "unchanged", "error", "skip"]
GradeStatus = Literal["new", "update", "unchanged", "error", "skip"]

ASSIGNMENT_HEADERS = ["student_email", "week", "teacher_email"]
GRADE_HEADERS = ["student_email", "week", "grade", "teacher_email"]


@dataclass
class AssignmentRow:
    index: int
    student_email: str
    week_no: Optional[int]
    teacher_email: str
    status: AssignmentStatus
    message: str | None = None
    student_id: str | None = None
    teacher_id: str | None = None
    current_teacher_id: str | None = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "student_email": self.student_email,
            "week_no": self.week_no,
            "teacher_email": self.teacher_email,
            "status": self.status,
            "message": self.message,
            "student_id": self.student_id,
            "teacher_id": self.teacher_id,
            "current_teacher_id": self.current_teacher_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "AssignmentRow":
        return cls(
            index=int(data["index"]),
            student_email=str(data["student_email"]),
            week_no=int(data["week_no"]) if data.get("week_no") is not None else None,
            teacher_email=str(data["teacher_email"]),
            status=str(data["status"]),
            message=data.get("message"),
            student_id=data.get("student_id"),
            teacher_id=data.get("teacher_id"),
            current_teacher_id=data.get("current_teacher_id"),
        )


@dataclass
class GradeRow:
    index: int
    student_email: str
    week_no: Optional[int]
    teacher_email: str
    grade: Optional[int]
    status: GradeStatus
    message: str | None = None
    student_id: str | None = None
    teacher_id: str | None = None
    current_grade: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "student_email": self.student_email,
            "week_no": self.week_no,
            "teacher_email": self.teacher_email,
            "grade": self.grade,
            "status": self.status,
            "message": self.message,
            "student_id": self.student_id,
            "teacher_id": self.teacher_id,
            "current_grade": self.current_grade,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "GradeRow":
        return cls(
            index=int(data["index"]),
            student_email=str(data["student_email"]),
            week_no=int(data["week_no"]) if data.get("week_no") is not None else None,
            teacher_email=str(data["teacher_email"]),
            grade=int(data["grade"]) if data.get("grade") is not None else None,
            status=str(data["status"]),
            message=data.get("message"),
            student_id=data.get("student_id"),
            teacher_id=data.get("teacher_id"),
            current_grade=(
                int(data["current_grade"])
                if data.get("current_grade") is not None
                else None
            ),
        )


@dataclass
class AssignmentPreview:
    rows: List[AssignmentRow]

    def summary(self) -> Dict[str, int]:
        totals: Dict[str, int] = {
            "total": 0,
            "new": 0,
            "update": 0,
            "unchanged": 0,
            "error": 0,
            "skip": 0,
        }
        for row in self.rows:
            totals["total"] += 1
            totals[row.status] += 1
        return totals

    def to_dict(self) -> Dict[str, object]:
        return {"rows": [row.to_dict() for row in self.rows]}

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "AssignmentPreview":
        rows = [AssignmentRow.from_dict(item) for item in data.get("rows", [])]
        return cls(rows=rows)

    def errors_csv(self) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["row_index", "kind", "message"])
        for row in self.rows:
            if row.status == "error":
                writer.writerow([row.index, "assignment", row.message or "error"])
        return buf.getvalue().encode("utf-8")

    def log_csv(self) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "row_index",
                "status",
                "message",
                "student_email",
                "week",
                "teacher_email",
            ]
        )
        for row in self.rows:
            writer.writerow(
                [
                    row.index,
                    row.status,
                    row.message or "",
                    row.student_email,
                    row.week_no if row.week_no is not None else "",
                    row.teacher_email,
                ]
            )
        return buf.getvalue().encode("utf-8")


@dataclass
class GradePreview:
    rows: List[GradeRow]

    def summary(self) -> Dict[str, int]:
        totals: Dict[str, int] = {
            "total": 0,
            "new": 0,
            "update": 0,
            "unchanged": 0,
            "error": 0,
            "skip": 0,
        }
        for row in self.rows:
            totals["total"] += 1
            totals[row.status] += 1
        return totals

    def to_dict(self) -> Dict[str, object]:
        return {"rows": [row.to_dict() for row in self.rows]}

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "GradePreview":
        rows = [GradeRow.from_dict(item) for item in data.get("rows", [])]
        return cls(rows=rows)

    def errors_csv(self) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["row_index", "kind", "message"])
        for row in self.rows:
            if row.status == "error":
                writer.writerow([row.index, "grade", row.message or "error"])
        return buf.getvalue().encode("utf-8")

    def log_csv(self) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "row_index",
                "status",
                "message",
                "student_email",
                "week",
                "grade",
                "teacher_email",
            ]
        )
        for row in self.rows:
            writer.writerow(
                [
                    row.index,
                    row.status,
                    row.message or "",
                    row.student_email,
                    row.week_no if row.week_no is not None else "",
                    row.grade if row.grade is not None else "",
                    row.teacher_email,
                ]
            )
        return buf.getvalue().encode("utf-8")


@dataclass
class ImportApplyResult:
    applied: int
    unchanged: int
    errors: int
    skipped: int


def _decode_csv(content: bytes) -> csv.reader:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
    return csv.reader(io.StringIO(text))


def _collect_headers(
    reader: csv.reader, expected: Iterable[str]
) -> tuple[bool, List[str]]:
    try:
        headers = next(reader)
    except StopIteration:
        return False, []
    return headers == list(expected), headers


def preview_assignments(content: bytes) -> AssignmentPreview:
    reader = _decode_csv(content)
    ok, headers = _collect_headers(reader, ASSIGNMENT_HEADERS)
    if not ok:
        rows = [
            AssignmentRow(
                index=0,
                student_email="",
                week_no=None,
                teacher_email="",
                status="error",
                message=(
                    "Ожидаются заголовки: " + ",".join(ASSIGNMENT_HEADERS)
                    if headers
                    else "Файл пуст или без заголовков"
                ),
            )
        ]
        return AssignmentPreview(rows=rows)

    # Preload course weeks for validation
    with db() as conn:
        week_rows = conn.execute("SELECT week_no FROM weeks").fetchall()
        valid_weeks = {int(r[0]) for r in week_rows}
        user_cache: Dict[tuple[str, str], Optional[str]] = {}
        assignment_cache: Dict[tuple[str, int], Optional[str]] = {}

        rows: List[AssignmentRow] = []
        seen_pairs: set[tuple[str, int]] = set()
        for idx, csv_row in enumerate(reader, start=1):
            if len(csv_row) < len(ASSIGNMENT_HEADERS):
                csv_row = csv_row + [""] * (len(ASSIGNMENT_HEADERS) - len(csv_row))
            elif len(csv_row) > len(ASSIGNMENT_HEADERS):
                csv_row = csv_row[: len(ASSIGNMENT_HEADERS)]
            student_email = (csv_row[0] or "").strip()
            week_raw = (csv_row[1] or "").strip()
            teacher_email = (csv_row[2] or "").strip()

            if not student_email and not week_raw and not teacher_email:
                rows.append(
                    AssignmentRow(
                        index=idx,
                        student_email="",
                        week_no=None,
                        teacher_email="",
                        status="skip",
                        message="пустая строка",
                    )
                )
                continue

            week_no: Optional[int]
            try:
                week_no = int(week_raw)
            except Exception:
                week_no = None

            if not student_email:
                rows.append(
                    AssignmentRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        status="error",
                        message="student_email обязателен",
                    )
                )
                continue
            if week_no is None:
                rows.append(
                    AssignmentRow(
                        index=idx,
                        student_email=student_email,
                        week_no=None,
                        teacher_email=teacher_email,
                        status="error",
                        message="week должен быть целым числом",
                    )
                )
                continue
            if week_no not in valid_weeks:
                rows.append(
                    AssignmentRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        status="error",
                        message="неделя отсутствует в расписании",
                    )
                )
                continue
            if not teacher_email:
                rows.append(
                    AssignmentRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        status="error",
                        message="teacher_email обязателен",
                    )
                )
                continue

            pair_key = (student_email.lower(), week_no)
            if pair_key in seen_pairs:
                rows.append(
                    AssignmentRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        status="error",
                        message="дубликат строки для пары student+week",
                    )
                )
                continue
            seen_pairs.add(pair_key)

            student_id = _resolve_user_id(
                conn, user_cache, role="student", email=student_email
            )
            if student_id is None:
                rows.append(
                    AssignmentRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        status="error",
                        message="студент не найден",
                    )
                )
                continue
            if student_id == "__ambiguous__":
                rows.append(
                    AssignmentRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        status="error",
                        message="несколько студентов с таким email",
                    )
                )
                continue

            teacher_id = _resolve_user_id(
                conn,
                user_cache,
                role="teacher",
                email=teacher_email,
            )
            if teacher_id is None:
                # allow owner to act as teacher
                teacher_id = _resolve_user_id(
                    conn, user_cache, role="owner", email=teacher_email
                )
            if teacher_id is None:
                rows.append(
                    AssignmentRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        status="error",
                        message="преподаватель не найден",
                    )
                )
                continue
            if teacher_id == "__ambiguous__":
                rows.append(
                    AssignmentRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        status="error",
                        message="несколько преподавателей с таким email",
                    )
                )
                continue

            current_key = (student_id, week_no)
            if current_key not in assignment_cache:
                cur = conn.execute(
                    (
                        "SELECT teacher_id FROM teacher_student_assignments "
                        "WHERE student_id=? AND week_no=?"
                    ),
                    (student_id, week_no),
                ).fetchone()
                assignment_cache[current_key] = cur[0] if cur else None
            current_teacher_id = assignment_cache[current_key]

            if current_teacher_id is None:
                status: AssignmentStatus = "new"
                message = "будет создано"
            elif str(current_teacher_id) == str(teacher_id):
                status = "unchanged"
                message = "без изменений"
            else:
                status = "update"
                message = "будет обновлено"

            rows.append(
                AssignmentRow(
                    index=idx,
                    student_email=student_email,
                    week_no=week_no,
                    teacher_email=teacher_email,
                    status=status,
                    message=message,
                    student_id=student_id,
                    teacher_id=teacher_id,
                    current_teacher_id=current_teacher_id,
                )
            )

    return AssignmentPreview(rows=rows)


def apply_assignments(preview: AssignmentPreview) -> ImportApplyResult:
    now = int(time.time())
    to_apply = [row for row in preview.rows if row.status in {"new", "update"}]
    if not to_apply:
        return ImportApplyResult(
            applied=0,
            unchanged=sum(1 for r in preview.rows if r.status == "unchanged"),
            errors=sum(1 for r in preview.rows if r.status == "error"),
            skipped=sum(1 for r in preview.rows if r.status == "skip"),
        )

    applied = 0
    runtime_errors = 0

    with db() as conn:
        conn.execute("BEGIN")
        try:
            for row in to_apply:
                if not row.student_id or not row.teacher_id or row.week_no is None:
                    runtime_errors += 1
                    row.status = "error"
                    row.message = "неполные данные при применении"
                    continue
                current = conn.execute(
                    (
                        "SELECT teacher_id FROM teacher_student_assignments "
                        "WHERE student_id=? AND week_no=?"
                    ),
                    (row.student_id, row.week_no),
                ).fetchone()
                current_teacher_id = current[0] if current else None
                if row.status == "update" and str(current_teacher_id) != str(
                    row.current_teacher_id
                ):
                    runtime_errors += 1
                    row.status = "error"
                    row.message = "назначение изменилось — обновите предпросмотр"
                    continue
                conn.execute(
                    (
                        "INSERT INTO teacher_student_assignments(week_no, teacher_id, student_id, created_at_utc) "
                        "VALUES(?,?,?,?) "
                        "ON CONFLICT(week_no, student_id) DO UPDATE SET "
                        "teacher_id=excluded.teacher_id, created_at_utc=excluded.created_at_utc"
                    ),
                    (row.week_no, row.teacher_id, row.student_id, now),
                )
                applied += 1
                row.message = "применено"
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    unchanged = sum(1 for r in preview.rows if r.status == "unchanged")
    skipped = sum(1 for r in preview.rows if r.status == "skip")
    errors = sum(1 for r in preview.rows if r.status == "error") + runtime_errors
    return ImportApplyResult(
        applied=applied, unchanged=unchanged, errors=errors, skipped=skipped
    )


def preview_grades(content: bytes) -> GradePreview:
    reader = _decode_csv(content)
    ok, headers = _collect_headers(reader, GRADE_HEADERS)
    if not ok:
        rows = [
            GradeRow(
                index=0,
                student_email="",
                week_no=None,
                teacher_email="",
                grade=None,
                status="error",
                message=(
                    "Ожидаются заголовки: " + ",".join(GRADE_HEADERS)
                    if headers
                    else "Файл пуст или без заголовков"
                ),
            )
        ]
        return GradePreview(rows=rows)

    with db() as conn:
        week_rows = conn.execute("SELECT week_no FROM weeks").fetchall()
        valid_weeks = {int(r[0]) for r in week_rows}
        user_cache: Dict[tuple[str, str], Optional[str]] = {}
        assignment_cache: Dict[tuple[str, int], Optional[str]] = {}
        grade_cache: Dict[tuple[str, int], Optional[int]] = {}

        rows: List[GradeRow] = []
        seen_pairs: set[tuple[str, int]] = set()
        for idx, csv_row in enumerate(reader, start=1):
            if len(csv_row) < len(GRADE_HEADERS):
                csv_row = csv_row + [""] * (len(GRADE_HEADERS) - len(csv_row))
            elif len(csv_row) > len(GRADE_HEADERS):
                csv_row = csv_row[: len(GRADE_HEADERS)]

            student_email = (csv_row[0] or "").strip()
            week_raw = (csv_row[1] or "").strip()
            grade_raw = (csv_row[2] or "").strip()
            teacher_email = (csv_row[3] or "").strip()

            if (
                not student_email
                and not week_raw
                and not grade_raw
                and not teacher_email
            ):
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email="",
                        week_no=None,
                        teacher_email="",
                        grade=None,
                        status="skip",
                        message="пустая строка",
                    )
                )
                continue

            try:
                week_no = int(week_raw)
            except Exception:
                week_no = None

            try:
                grade_value = int(grade_raw)
            except Exception:
                grade_value = None

            if not student_email:
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="student_email обязателен",
                    )
                )
                continue
            if week_no is None:
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=None,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="week должен быть целым числом",
                    )
                )
                continue
            if week_no not in valid_weeks:
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="неделя отсутствует в расписании",
                    )
                )
                continue
            if grade_value is None:
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=None,
                        status="error",
                        message="grade должен быть целым в диапазоне 1..10",
                    )
                )
                continue
            if not (1 <= grade_value <= 10):
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="grade вне диапазона 1..10",
                    )
                )
                continue
            if not teacher_email:
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="teacher_email обязателен",
                    )
                )
                continue

            pair_key = (student_email.lower(), week_no)
            if pair_key in seen_pairs:
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="дубликат строки для пары student+week",
                    )
                )
                continue
            seen_pairs.add(pair_key)

            student_id = _resolve_user_id(
                conn, user_cache, role="student", email=student_email
            )
            if student_id is None:
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="студент не найден",
                    )
                )
                continue
            if student_id == "__ambiguous__":
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="несколько студентов с таким email",
                    )
                )
                continue

            teacher_id = _resolve_user_id(
                conn, user_cache, role="teacher", email=teacher_email
            )
            if teacher_id is None:
                teacher_id = _resolve_user_id(
                    conn, user_cache, role="owner", email=teacher_email
                )
            if teacher_id is None:
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="преподаватель не найден",
                    )
                )
                continue
            if teacher_id == "__ambiguous__":
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="несколько преподавателей с таким email",
                    )
                )
                continue

            assign_key = (student_id, week_no)
            if assign_key not in assignment_cache:
                cur = conn.execute(
                    (
                        "SELECT teacher_id FROM teacher_student_assignments "
                        "WHERE student_id=? AND week_no=?"
                    ),
                    (student_id, week_no),
                ).fetchone()
                assignment_cache[assign_key] = cur[0] if cur else None
            assigned_teacher_id = assignment_cache[assign_key]
            if assigned_teacher_id is None:
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="нет назначения для этой недели",
                    )
                )
                continue
            if str(assigned_teacher_id) != str(teacher_id):
                rows.append(
                    GradeRow(
                        index=idx,
                        student_email=student_email,
                        week_no=week_no,
                        teacher_email=teacher_email,
                        grade=grade_value,
                        status="error",
                        message="teacher не совпадает с матрицей",
                    )
                )
                continue

            grade_key = (student_id, week_no)
            if grade_key not in grade_cache:
                cur = conn.execute(
                    (
                        "SELECT grade FROM submissions WHERE student_id=? AND week_no=? "
                        "ORDER BY id DESC LIMIT 1"
                    ),
                    (student_id, week_no),
                ).fetchone()
                grade_cache[grade_key] = (
                    int(cur[0]) if cur and str(cur[0]).isdigit() else None
                )
            current_grade = grade_cache[grade_key]

            if current_grade is None:
                status: GradeStatus = "new"
                message = "будет создано"
            elif current_grade == grade_value:
                status = "unchanged"
                message = "без изменений"
            else:
                status = "update"
                message = "будет обновлено"

            rows.append(
                GradeRow(
                    index=idx,
                    student_email=student_email,
                    week_no=week_no,
                    teacher_email=teacher_email,
                    grade=grade_value,
                    status=status,
                    message=message,
                    student_id=student_id,
                    teacher_id=teacher_id,
                    current_grade=current_grade,
                )
            )

    return GradePreview(rows=rows)


def apply_grades(preview: GradePreview) -> ImportApplyResult:
    to_apply = [row for row in preview.rows if row.status in {"new", "update"}]
    if not to_apply:
        return ImportApplyResult(
            applied=0,
            unchanged=sum(1 for r in preview.rows if r.status == "unchanged"),
            errors=sum(1 for r in preview.rows if r.status == "error"),
            skipped=sum(1 for r in preview.rows if r.status == "skip"),
        )

    applied = 0
    runtime_errors = 0

    with db() as conn:
        for row in to_apply:
            if (
                not row.student_id
                or not row.teacher_id
                or row.week_no is None
                or row.grade is None
            ):
                runtime_errors += 1
                row.status = "error"
                row.message = "неполные данные при применении"
                continue
            current_teacher = conn.execute(
                (
                    "SELECT teacher_id FROM teacher_student_assignments "
                    "WHERE student_id=? AND week_no=?"
                ),
                (row.student_id, row.week_no),
            ).fetchone()
            current_teacher_id = current_teacher[0] if current_teacher else None
            if str(current_teacher_id) != str(row.teacher_id):
                runtime_errors += 1
                row.status = "error"
                row.message = "назначение изменилось — обновите матрицу"
                continue
            current_grade_row = conn.execute(
                (
                    "SELECT grade FROM submissions WHERE student_id=? AND week_no=? "
                    "ORDER BY id DESC LIMIT 1"
                ),
                (row.student_id, row.week_no),
            ).fetchone()
            current_grade = (
                int(current_grade_row[0])
                if current_grade_row and str(current_grade_row[0]).isdigit()
                else None
            )
            if row.status == "update" and current_grade != row.current_grade:
                runtime_errors += 1
                row.status = "error"
                row.message = "оценка изменилась — обновите предпросмотр"
                continue
            if row.status == "new" and current_grade is not None:
                runtime_errors += 1
                row.status = "error"
                row.message = "оценка уже существует"
                continue
            set_week_grade(
                student_id=row.student_id,
                week_no=row.week_no,
                reviewer_id=row.teacher_id,
                score_int=row.grade,
                origin="owner_import",
            )
            applied += 1
            row.message = "применено"

    unchanged = sum(1 for r in preview.rows if r.status == "unchanged")
    skipped = sum(1 for r in preview.rows if r.status == "skip")
    errors = sum(1 for r in preview.rows if r.status == "error") + runtime_errors
    return ImportApplyResult(
        applied=applied, unchanged=unchanged, errors=errors, skipped=skipped
    )


def assignments_template() -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(ASSIGNMENT_HEADERS)
    return buf.getvalue().encode("utf-8")


def grades_template() -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(GRADE_HEADERS)
    return buf.getvalue().encode("utf-8")


def _resolve_user_id(
    conn, cache: Dict[tuple[str, str], Optional[str]], *, role: str, email: str
) -> Optional[str]:
    key = (role, email.lower())
    if key in cache:
        return cache[key]
    rows = conn.execute(
        ("SELECT id FROM users WHERE role=? AND LOWER(COALESCE(email,''))=LOWER(?)"),
        (role, email),
    ).fetchall()
    if not rows:
        cache[key] = None
        return None
    if len(rows) > 1:
        cache[key] = "__ambiguous__"
        return "__ambiguous__"
    cache[key] = str(rows[0][0])
    return cache[key]
