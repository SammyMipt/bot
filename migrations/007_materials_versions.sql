PRAGMA foreign_keys=ON;

-- EPIC-4/Owner: add material types and versioning to materials table
-- - type: one of ('p','m','n','s','v') mapping to owner UI
-- - is_active: 1 active per (week_id,type), others considered archive
-- - version: monotonically increasing per (week_id,type)

-- 1) Add new columns (SQLite allows ADD COLUMN without backfill expressions)
ALTER TABLE materials ADD COLUMN type TEXT CHECK(type IN ('p','m','n','s','v'));
ALTER TABLE materials ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE materials ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

-- 2) Backfill existing rows: default type='p' when NULL
UPDATE materials SET type='p' WHERE type IS NULL;

-- 3) Create indexes to speed up queries
CREATE INDEX IF NOT EXISTS idx_materials_week_type_active ON materials(week_id, type, is_active);
CREATE INDEX IF NOT EXISTS idx_materials_week_type_version ON materials(week_id, type, version);
