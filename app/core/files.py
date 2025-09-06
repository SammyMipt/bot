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
    # store content-addressed blob under base/.blobs/<hash>
    blobs_dir = os.path.join(base, ".blobs")
    os.makedirs(blobs_dir, exist_ok=True)
    blob_path = os.path.join(blobs_dir, digest)
    existed = os.path.exists(blob_path)
    if not existed:
        with open(blob_path, "wb") as f:
            f.write(data)
    # Do not create extra placeholder files; store exactly one content file.
    size_bytes = os.path.getsize(blob_path)
    return SavedBlob(digest, blob_path, size_bytes, existed)


def ensure_parent_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def link_or_copy(src: str, dst: str) -> None:
    """Create a hardlink from src to dst; fallback to copy on failure."""
    ensure_parent_dir(dst)
    try:
        if os.path.exists(dst):
            os.remove(dst)
        os.link(src, dst)
    except Exception:
        # fallback to copy
        import shutil

        shutil.copy2(src, dst)


def move_file(src: str, dst: str) -> None:
    ensure_parent_dir(dst)
    import os as _os

    try:
        _os.replace(src, dst)
    except Exception:
        import shutil as _shutil

        _shutil.move(src, dst)
