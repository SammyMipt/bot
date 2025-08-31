import hashlib
import os
from dataclasses import dataclass
from typing import Optional

BASE_VAR = os.environ.get("APP_VAR_DIR", "var")
MATERIALS_DIR = os.path.join(BASE_VAR, "materials")
SUBMISSIONS_DIR = os.path.join(BASE_VAR, "submissions")

os.makedirs(MATERIALS_DIR, exist_ok=True)
os.makedirs(SUBMISSIONS_DIR, exist_ok=True)


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def safe_filename(name: str) -> str:
    bad = ["..", "/", "\\", "\0", ":", "*", "?", '"', "<", ">", "|"]
    for b in bad:
        name = name.replace(b, "_")
    return name.strip() or "file.bin"


@dataclass
class SavedBlob:
    sha256: str
    path: str
    size_bytes: int
    existed: bool


def save_blob(
    data: bytes, prefix: str, suggested_name: Optional[str] = None
) -> SavedBlob:
    digest = sha256_bytes(data)
    base = MATERIALS_DIR if prefix == "materials" else SUBMISSIONS_DIR
    blob_path = os.path.join(base, digest)
    existed = os.path.exists(blob_path)
    if not existed:
        with open(blob_path, "wb") as f:
            f.write(data)
    if suggested_name:
        safe = safe_filename(suggested_name)
        hint = os.path.join(base, f"{digest}__{safe}")
        if not os.path.exists(hint):
            try:
                with open(hint, "wb") as f:
                    f.write(b"")
            except Exception:
                pass
    size_bytes = os.path.getsize(blob_path)
    return SavedBlob(digest, blob_path, size_bytes, existed)
