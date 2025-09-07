from typing import Any, Awaitable, Callable, Dict

from aiogram.types import CallbackQuery, Message

from aiogram import BaseMiddleware
from app.core import auth, state_store


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
            # Real initiator identity
            real_tg_id = str(tg_user.id)
            principal = auth.get_user_by_tg(real_tg_id)

            # Try to read impersonation session for this Telegram user
            imp = None
            try:
                _, st = state_store.get(f"impersonate:{tg_user.id}")
                imp = st if st and st.get("exp", 0) >= state_store.now() else None
            except Exception:
                imp = None

            # Decide whether to swap actor or keep owner context
            is_owner_ui = (
                isinstance(event, CallbackQuery)
                and isinstance(event.data, str)
                and event.data.startswith("own:")
            )

            if principal and principal.role == "owner" and imp and not is_owner_ui:
                # Acting as target for non-owner UI while preserving principal
                target = (
                    auth.get_user_by_tg(str(imp.get("tg_id")))
                    if imp.get("tg_id")
                    else None
                )
                if target:
                    data["principal"] = principal
                    data["impersonation"] = {
                        "as_id": target.id,
                        "as_role": target.role,
                        "exp": imp.get("exp"),
                    }
                    data["actor"] = target
                    return await handler(event, data)

            # Default: no impersonation or owner UI — pass principal or guest
            if principal:
                data["actor"] = principal
            else:
                from app.core.auth import Identity

                data["actor"] = Identity(
                    id="guest",
                    role="guest",
                    tg_id=real_tg_id,
                    name=tg_user.full_name or None,
                )
        return await handler(event, data)
