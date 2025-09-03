DROP TABLE IF EXISTS state_store;
CREATE TABLE IF NOT EXISTS state_store (
  key            TEXT PRIMARY KEY,
  role           TEXT,
  action         TEXT,
  params         TEXT,
  created_at_utc INTEGER NOT NULL,
  expires_at_utc INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_expiry ON state_store(expires_at_utc);
