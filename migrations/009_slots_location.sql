-- Add mode/location for slots to store room or video link
ALTER TABLE slots ADD COLUMN mode TEXT CHECK(mode IN ('online','offline'));
ALTER TABLE slots ADD COLUMN location TEXT;
