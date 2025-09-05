import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.backup import trigger_backup
from app.core.state_store import cleanup_expired

logger = logging.getLogger(__name__)


async def periodic_cleanup(interval_sec: int = 120) -> None:
    """Periodically remove expired state store entries."""
    while True:
        try:
            removed = cleanup_expired()
            if removed:
                logger.info("state-store cleaned %d expired entries", removed)
        except Exception:  # pragma: no cover - logging for debugging
            logger.exception("state-store cleanup failed")
        await asyncio.sleep(interval_sec)


def _seconds_until(hour: int, minute: int) -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


async def periodic_backup_daily(hour_utc: int = 3, minute_utc: int = 0) -> None:
    """Run auto backup daily at specified UTC time (default 03:00 UTC)."""
    # Initial delay until the next scheduled time
    delay = _seconds_until(hour_utc, minute_utc)
    await asyncio.sleep(delay)
    while True:
        try:
            meta = trigger_backup("auto")
            logger.info(
                "backup done: id=%s type=%s objects=%d bytes=%d",
                getattr(meta, "backup_id", "-"),
                getattr(meta, "type", "-"),
                getattr(meta, "objects_count", 0),
                getattr(meta, "bytes_total", 0),
            )
        except Exception:
            logger.exception("daily backup failed")
        # Sleep ~24h until next run
        await asyncio.sleep(24 * 3600)
