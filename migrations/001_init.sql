PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_id            TEXT UNIQUE,
  role             TEXT NOT NULL CHECK(role IN ('owner','teacher','student')),
  name             TEXT,
  created_at_utc   INTEGER NOT NULL,
  updated_at_utc   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

CREATE TABLE IF NOT EXISTS weeks (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  week_no          INTEGER UNIQUE NOT NULL,
  title            TEXT,
  created_at_utc   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  code             TEXT UNIQUE,
  title            TEXT NOT NULL,
  week_no          INTEGER,
  deadline_ts_utc  INTEGER,
  created_at_utc   INTEGER NOT NULL,
  FOREIGN KEY (week_no) REFERENCES weeks(week_no) ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS slots (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  starts_at_utc    INTEGER NOT NULL,
  duration_min     INTEGER NOT NULL,
  capacity         INTEGER NOT NULL,
  status           TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed','canceled')),
  created_by       INTEGER NOT NULL,
  created_at_utc   INTEGER NOT NULL,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_slots_starts ON slots(starts_at_utc);

CREATE TABLE IF NOT EXISTS slot_enrollments (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  slot_id          INTEGER NOT NULL,
  user_id          INTEGER NOT NULL,
  status           TEXT NOT NULL DEFAULT 'booked' CHECK(status IN ('booked','canceled','attended','no_show')),
  booked_at_utc    INTEGER NOT NULL,
  UNIQUE(slot_id, user_id),
  FOREIGN KEY (slot_id) REFERENCES slots(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS materials (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  assignment_id    INTEGER,
  path             TEXT NOT NULL,
  sha256           TEXT NOT NULL,
  size_bytes       INTEGER NOT NULL,
  mime             TEXT,
  uploaded_by      INTEGER NOT NULL,
  created_at_utc   INTEGER NOT NULL,
  FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE SET NULL,
  FOREIGN KEY (uploaded_by)  REFERENCES users(id)       ON DELETE RESTRICT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_materials_hash ON materials(sha256, size_bytes);
CREATE INDEX IF NOT EXISTS idx_materials_assignment ON materials(assignment_id);

CREATE TABLE IF NOT EXISTS submissions (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  assignment_id    INTEGER NOT NULL,
  student_id       INTEGER NOT NULL,
  path             TEXT NOT NULL,
  sha256           TEXT NOT NULL,
  size_bytes       INTEGER NOT NULL,
  submitted_at_utc INTEGER NOT NULL,
  FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE,
  FOREIGN KEY (student_id)   REFERENCES users(id)       ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_subm_assignment ON submissions(assignment_id);
CREATE INDEX IF NOT EXISTS idx_subm_student    ON submissions(student_id);

CREATE TABLE IF NOT EXISTS grades (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  assignment_id    INTEGER NOT NULL,
  student_id       INTEGER NOT NULL,
  grader_id        INTEGER NOT NULL,
  grade_value      TEXT,
  comment          TEXT,
  graded_at_utc    INTEGER NOT NULL,
  UNIQUE(assignment_id, student_id),
  FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE,
  FOREIGN KEY (student_id)   REFERENCES users(id)       ON DELETE CASCADE,
  FOREIGN KEY (grader_id)    REFERENCES users(id)       ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc           INTEGER NOT NULL,
  request_id       TEXT,
  actor_id         INTEGER,
  as_user_id       INTEGER,
  as_role          TEXT,
  event            TEXT NOT NULL,
  object_type      TEXT,
  object_id        INTEGER,
  meta_json        TEXT,
  FOREIGN KEY (actor_id)   REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (as_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_event_ts ON audit_log(event, ts_utc);

CREATE TABLE IF NOT EXISTS state_store (
  key              TEXT PRIMARY KEY,
  role             TEXT,
  value_json       TEXT NOT NULL,
  created_at_utc   INTEGER NOT NULL,
  expires_at_utc   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_expiry ON state_store(expires_at_utc);

CREATE TABLE IF NOT EXISTS file_index (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256           TEXT NOT NULL,
  size_bytes       INTEGER NOT NULL,
  refcount         INTEGER NOT NULL DEFAULT 1,
  UNIQUE(sha256, size_bytes)
);

CREATE TABLE IF NOT EXISTS system_backups (
  id               INTEGER PRIMARY KEY CHECK (id = 1),
  last_full_ts_utc INTEGER,
  last_inc_ts_utc  INTEGER,
  updated_at_utc   INTEGER
);
INSERT OR IGNORE INTO system_backups(id) VALUES(1);
