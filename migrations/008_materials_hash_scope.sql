PRAGMA foreign_keys=ON;

-- Adjust uniqueness scope for materials checksum to be per (week_id,type),
-- allowing identical files/links to appear in different weeks/types.
DROP INDEX IF EXISTS idx_materials_hash;
CREATE UNIQUE INDEX IF NOT EXISTS idx_materials_week_type_hash
  ON materials(week_id, type, sha256, size_bytes);
