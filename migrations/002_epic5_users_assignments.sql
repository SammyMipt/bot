PRAGMA foreign_keys=ON;

-- EPIC-5 T1: Extend users with new optional attributes
ALTER TABLE users ADD COLUMN email TEXT;
ALTER TABLE users ADD COLUMN group_name TEXT;  -- for students
ALTER TABLE users ADD COLUMN tef INTEGER;      -- for teachers (load factor)
ALTER TABLE users ADD COLUMN capacity INTEGER; -- for teachers (max students)
ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;

-- Ensure existing rows have is_active=1 (safety for older SQLite behaviors)
UPDATE users SET is_active = 1 WHERE is_active IS NULL;

-- Indexes
-- role index already exists from 001_init.sql; add email index if present
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- EPIC-5 T1: Teacher-student weekly assignments
CREATE TABLE IF NOT EXISTS teacher_student_assignments (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  week_no          INTEGER NOT NULL,
  teacher_id       INTEGER NOT NULL,
  student_id       INTEGER NOT NULL,
  created_at_utc   INTEGER NOT NULL,
  UNIQUE(week_no, student_id),
  FOREIGN KEY (week_no)    REFERENCES weeks(week_no) ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (teacher_id) REFERENCES users(id)     ON DELETE CASCADE,
  FOREIGN KEY (student_id) REFERENCES users(id)     ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tsa_teacher_week ON teacher_student_assignments(teacher_id, week_no);
CREATE INDEX IF NOT EXISTS idx_tsa_week         ON teacher_student_assignments(week_no);
