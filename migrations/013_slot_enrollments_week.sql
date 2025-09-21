-- Add week_no to slot_enrollments and helpful indexes
PRAGMA foreign_keys=ON;

-- 1) Add week_no column if missing
ALTER TABLE slot_enrollments ADD COLUMN week_no INTEGER;

-- 2) Indexes to speed up lookups
CREATE INDEX IF NOT EXISTS idx_se_user_week_alive
  ON slot_enrollments(user_id, week_no, status);

-- 3) Ensure single active booking per (student, week)
-- Partial unique index: only for status='booked'
CREATE UNIQUE INDEX IF NOT EXISTS uidx_se_one_active_per_week
  ON slot_enrollments(user_id, week_no)
  WHERE status='booked';
