from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.core import auth
from app.core.config import cfg


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = None
        if isinstance(event, Message):
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery):
            tg_user = event.from_user
        if tg_user:
            # EPIC-5: avoid auto-creating users; attach existing or provide guest identity
            eff_tg_id = cfg.auth_tg_override or str(tg_user.id)
            existing = auth.get_user_by_tg(eff_tg_id)
            if existing:
                data["actor"] = existing
            else:
                from app.core.auth import Identity

                data["actor"] = Identity(
                    id="guest",
                    role="guest",
                    tg_id=eff_tg_id,
                    name=tg_user.full_name or None,
                )
        return await handler(event, data)
