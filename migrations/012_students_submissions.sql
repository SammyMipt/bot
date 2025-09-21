-- students_submissions: canonical store for student weekly submission files
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS students_submissions (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  student_id       TEXT NOT NULL,
  week_no          INTEGER NOT NULL,
  sha256           TEXT NOT NULL,
  size_bytes       INTEGER NOT NULL,
  path             TEXT NOT NULL,
  mime             TEXT,
  created_at_utc   INTEGER NOT NULL,
  deleted_at_utc   INTEGER,
  FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (week_no)    REFERENCES weeks(week_no) ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ss_student_week ON students_submissions(student_id, week_no);
CREATE INDEX IF NOT EXISTS idx_ss_week_student ON students_submissions(week_no, student_id);
CREATE INDEX IF NOT EXISTS idx_ss_alive ON students_submissions(student_id, week_no, deleted_at_utc);
CREATE UNIQUE INDEX IF NOT EXISTS uidx_ss_dedup ON students_submissions(student_id, week_no, sha256, size_bytes)
  WHERE deleted_at_utc IS NULL;
