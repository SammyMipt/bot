-- Grades history table: records each grade set/change by teachers/owners
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS grades (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  student_id       TEXT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week_no          INTEGER   NOT NULL REFERENCES weeks(week_no) ON UPDATE CASCADE ON DELETE CASCADE,
  score_int        INTEGER   NOT NULL CHECK(score_int BETWEEN 1 AND 10),
  graded_by        TEXT      NOT NULL REFERENCES users(id) ON DELETE SET NULL,
  graded_at_utc    INTEGER   NOT NULL,
  prev_score_int   INTEGER,
  comment          TEXT,
  origin           TEXT      DEFAULT 'slot'
);

CREATE INDEX IF NOT EXISTS idx_grades_student_week ON grades(student_id, week_no, graded_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_grades_when ON grades(graded_at_utc);
