-- Add course meta table and extend weeks to match documentation
PRAGMA foreign_keys=ON;

-- Course table (single course)
CREATE TABLE IF NOT EXISTS course (
  id               INTEGER PRIMARY KEY CHECK (id = 1),
  name             TEXT NOT NULL,
  created_at_utc   INTEGER NOT NULL,
  updated_at_utc   INTEGER NOT NULL
);
INSERT OR IGNORE INTO course(id, name, created_at_utc, updated_at_utc)
VALUES (1, 'Course', strftime('%s','now'), strftime('%s','now'));

-- Extend weeks with topic/description/deadline
ALTER TABLE weeks ADD COLUMN topic TEXT;
ALTER TABLE weeks ADD COLUMN description TEXT;
ALTER TABLE weeks ADD COLUMN deadline_ts_utc INTEGER;

-- Backfill from existing data where possible
UPDATE weeks
SET topic = COALESCE(topic, title);

UPDATE weeks
SET description = (
  SELECT a.title FROM assignments a
  WHERE a.week_no = weeks.week_no AND a.code = 'W' || weeks.week_no
)
WHERE description IS NULL;

UPDATE weeks
SET deadline_ts_utc = (
  SELECT a.deadline_ts_utc FROM assignments a
  WHERE a.week_no = weeks.week_no AND a.code = 'W' || weeks.week_no
)
WHERE deadline_ts_utc IS NULL;
