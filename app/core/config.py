import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


@dataclass
class Config:
    app_env: str = os.getenv("APP_ENV", "dev")
    tz: str = os.getenv("TZ", "UTC")
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    data_dir: str = os.getenv("DATA_DIR", "./var")
    sqlite_path: str = os.getenv("SQLITE_PATH", "./var/app.db")
    max_file_mb: int = int(os.getenv("MAX_FILE_MB", "20"))
    course_secret: str = os.getenv("COURSE_SECRET", "")
    auth_tg_override: str = os.getenv("AUTH_TG_OVERRIDE", "")
    telegram_owner_ids_raw: str = os.getenv("OWNERS_TELEGRAM_ID", "")

    @property
    def telegram_owner_ids(self) -> set[str]:
        raw = self.telegram_owner_ids_raw or ""
        parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
        return set(parts)


cfg = Config()
