import asyncio
import logging

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
