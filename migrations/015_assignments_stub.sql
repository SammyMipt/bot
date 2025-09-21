-- Recreate minimal assignments table to satisfy legacy FKs from submissions/materials
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS assignments (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  code             TEXT UNIQUE,
  title            TEXT,
  week_no          INTEGER,
  deadline_ts_utc  INTEGER,
  created_at_utc   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_assignments_week ON assignments(week_no);
