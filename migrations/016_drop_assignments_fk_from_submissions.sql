-- Remove legacy dependency on assignments from submissions
PRAGMA foreign_keys=OFF;

-- 1) Create new submissions table without assignment_id and any FK to assignments
CREATE TABLE IF NOT EXISTS submissions_new (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  week_no          INTEGER,
  student_id       TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','graded')),
  grade            TEXT,
  created_at_utc   INTEGER NOT NULL,
  reviewed_by      TEXT,
  reviewed_at_utc  INTEGER,
  FOREIGN KEY (week_no)       REFERENCES weeks(week_no) ON UPDATE CASCADE ON DELETE SET NULL,
  FOREIGN KEY (student_id)    REFERENCES users(id)    ON DELETE CASCADE,
  FOREIGN KEY (reviewed_by)   REFERENCES users(id)    ON DELETE SET NULL
);

-- 2) Backfill from old submissions
INSERT INTO submissions_new(
  id, week_no, student_id, status, grade, created_at_utc, reviewed_by, reviewed_at_utc
)
SELECT id, week_no, student_id, status, grade, created_at_utc, reviewed_by, reviewed_at_utc
FROM submissions;

-- 3) Swap tables
DROP TABLE submissions;
ALTER TABLE submissions_new RENAME TO submissions;

-- 4) Recreate indexes (without assignment index)
CREATE INDEX IF NOT EXISTS idx_subm_student_week ON submissions(student_id, week_no);

-- 5) Drop leftover assignments table if present
DROP TABLE IF EXISTS assignments;

PRAGMA foreign_keys=ON;
