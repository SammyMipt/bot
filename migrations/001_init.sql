PRAGMA foreign_keys=ON;

-- ========= USERS =========
CREATE TABLE IF NOT EXISTS users (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_id            TEXT UNIQUE,
  role             TEXT NOT NULL CHECK(role IN ('owner','teacher','student')),
  name             TEXT,
  created_at_utc   INTEGER NOT NULL,
  updated_at_utc   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- ========= WEEKS =========
CREATE TABLE IF NOT EXISTS weeks (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  week_no          INTEGER UNIQUE NOT NULL,
  title            TEXT,
  created_at_utc   INTEGER NOT NULL
);

-- ========= ASSIGNMENTS (оставляем — понадобится далее) =========
CREATE TABLE IF NOT EXISTS assignments (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  code             TEXT UNIQUE,
  title            TEXT NOT NULL,
  week_no          INTEGER,
  deadline_ts_utc  INTEGER,
  created_at_utc   INTEGER NOT NULL,
  FOREIGN KEY (week_no) REFERENCES weeks(week_no)
    ON UPDATE CASCADE ON DELETE SET NULL
);

-- ========= SLOTS / ENROLLMENTS (как было) =========
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

-- ========= MATERIALS (теперь могут быть недельными; + visibility) =========
CREATE TABLE IF NOT EXISTS materials (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  -- либо material для конкретного задания...
  assignment_id    INTEGER,
  -- ...либо material для недели напрямую:
  week_no          INTEGER,
  -- сам файл:
  path             TEXT NOT NULL,
  sha256           TEXT NOT NULL,
  size_bytes       INTEGER NOT NULL,
  mime             TEXT,
  -- видимость материала:
  visibility       TEXT NOT NULL DEFAULT 'public' CHECK(visibility IN ('public','teacher_only')),
  -- служебные поля:
  uploaded_by      INTEGER NOT NULL,
  created_at_utc   INTEGER NOT NULL,
  FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE SET NULL,
  FOREIGN KEY (week_no)       REFERENCES weeks(week_no) ON UPDATE CASCADE ON DELETE SET NULL,
  FOREIGN KEY (uploaded_by)   REFERENCES users(id)       ON DELETE RESTRICT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_materials_hash ON materials(sha256, size_bytes);
CREATE INDEX IF NOT EXISTS idx_materials_assignment ON materials(assignment_id);
CREATE INDEX IF NOT EXISTS idx_materials_week_no    ON materials(week_no);
CREATE INDEX IF NOT EXISTS idx_materials_visibility ON materials(visibility);

-- ========= SUBMISSIONS (сдача за неделю; многофайловая) =========
CREATE TABLE IF NOT EXISTS submissions (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  -- для совместимости оставим привязку к заданию (NULL в нашем потоке):
  assignment_id    INTEGER,
  -- основная модель EPIC-4: сдача по неделе
  week_no          INTEGER,
  student_id       INTEGER NOT NULL,
  status           TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','graded')),
  grade            TEXT,                 -- заглушка на оценку (можно 'pass'/'fail' или число/буква)
  created_at_utc   INTEGER NOT NULL,
  reviewed_by      INTEGER,
  reviewed_at_utc  INTEGER,
  FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE,
  FOREIGN KEY (week_no)       REFERENCES weeks(week_no) ON UPDATE CASCADE ON DELETE SET NULL,
  FOREIGN KEY (student_id)    REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (reviewed_by)   REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_subm_student_week ON submissions(student_id, week_no);
CREATE INDEX IF NOT EXISTS idx_subm_assignment   ON submissions(assignment_id);

-- ========= WEEK SUBMISSION FILES (много файлов в одной сдаче + мягкое удаление) =========
CREATE TABLE IF NOT EXISTS week_submission_files (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id    INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  sha256           TEXT NOT NULL,
  size_bytes       INTEGER NOT NULL,
  path             TEXT NOT NULL,
  mime             TEXT,
  created_at_utc   INTEGER NOT NULL,
  deleted_at_utc   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_wsf_submission ON week_submission_files(submission_id);
CREATE INDEX IF NOT EXISTS idx_wsf_alive      ON week_submission_files(submission_id, deleted_at_utc);
CREATE UNIQUE INDEX IF NOT EXISTS uidx_wsf_sub_sha ON week_submission_files(submission_id, sha256, size_bytes)
  WHERE deleted_at_utc IS NULL;

-- ========= AUDIT / STATE / FILE INDEX / SYSTEM BACKUPS (как было) =========
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
