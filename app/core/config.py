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


cfg = Config()
