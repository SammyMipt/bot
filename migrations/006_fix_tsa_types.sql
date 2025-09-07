PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;

-- Align teacher_student_assignments FK types to TEXT to match users.id (TEXT UUID)
CREATE TABLE IF NOT EXISTS teacher_student_assignments_new (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  week_no          INTEGER NOT NULL,
  teacher_id       TEXT NOT NULL,
  student_id       TEXT NOT NULL,
  created_at_utc   INTEGER NOT NULL,
  UNIQUE(week_no, student_id),
  FOREIGN KEY (week_no)    REFERENCES weeks(week_no) ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (teacher_id) REFERENCES users(id)     ON DELETE CASCADE,
  FOREIGN KEY (student_id) REFERENCES users(id)     ON DELETE CASCADE
);

INSERT INTO teacher_student_assignments_new(id, week_no, teacher_id, student_id, created_at_utc)
SELECT id, week_no, CAST(teacher_id AS TEXT), CAST(student_id AS TEXT), created_at_utc
FROM teacher_student_assignments;

DROP TABLE teacher_student_assignments;
ALTER TABLE teacher_student_assignments_new RENAME TO teacher_student_assignments;

CREATE INDEX IF NOT EXISTS idx_tsa_teacher_week ON teacher_student_assignments(teacher_id, week_no);
CREATE INDEX IF NOT EXISTS idx_tsa_week         ON teacher_student_assignments(week_no);

COMMIT;
PRAGMA foreign_keys=ON;
