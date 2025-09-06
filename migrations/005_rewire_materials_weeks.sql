PRAGMA foreign_keys=OFF;

-- 1) Create new materials table bound to weeks.id instead of assignment/week_no
CREATE TABLE IF NOT EXISTS materials_new (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  week_id          INTEGER NOT NULL,
  path             TEXT NOT NULL,
  sha256           TEXT NOT NULL,
  size_bytes       INTEGER NOT NULL,
  mime             TEXT,
  visibility       TEXT NOT NULL DEFAULT 'public' CHECK(visibility IN ('public','teacher_only')),
  uploaded_by      TEXT NOT NULL,
  created_at_utc   INTEGER NOT NULL,
  FOREIGN KEY (week_id)    REFERENCES weeks(id)   ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (uploaded_by) REFERENCES users(id)  ON DELETE RESTRICT
);

-- 2) Backfill data from old materials using week_no or assignments
INSERT INTO materials_new(id, week_id, path, sha256, size_bytes, mime, visibility, uploaded_by, created_at_utc)
SELECT m.id,
       COALESCE(w.id, w2.id) AS week_id,
       m.path, m.sha256, m.size_bytes, m.mime, m.visibility, m.uploaded_by, m.created_at_utc
FROM materials m
LEFT JOIN weeks w ON w.week_no = m.week_no
LEFT JOIN assignments a ON a.id = m.assignment_id
LEFT JOIN weeks w2 ON w2.week_no = a.week_no
WHERE COALESCE(w.id, w2.id) IS NOT NULL;

-- 3) Swap tables
DROP TABLE materials;
ALTER TABLE materials_new RENAME TO materials;

-- 4) Recreate indexes
CREATE UNIQUE INDEX IF NOT EXISTS idx_materials_hash ON materials(sha256, size_bytes);
CREATE INDEX IF NOT EXISTS idx_materials_week_id ON materials(week_id);
CREATE INDEX IF NOT EXISTS idx_materials_visibility ON materials(visibility);

-- 5) Drop assignments table (no longer used)
DROP TABLE IF EXISTS assignments;

PRAGMA foreign_keys=ON;
