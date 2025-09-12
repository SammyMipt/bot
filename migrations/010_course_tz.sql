-- Add course timezone column with UTC default
PRAGMA foreign_keys=ON;

-- Ensure course table exists per 004 migration, then extend
ALTER TABLE course ADD COLUMN tz TEXT NOT NULL DEFAULT 'UTC';

-- Normalize existing single row to have tz set (id=1)
UPDATE course SET tz = COALESCE(NULLIF(tz, ''), 'UTC') WHERE id=1;
