PRAGMA foreign_keys=ON;

-- Add optional timezone column for users (IANA tz name)
ALTER TABLE users ADD COLUMN tz TEXT;

-- Default existing users to course timezone when available
UPDATE users
SET tz = (
  SELECT tz FROM course WHERE id = 1
)
WHERE tz IS NULL OR tz = '';
