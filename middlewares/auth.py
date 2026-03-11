from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from config import Config
import db.queries as db


class AuthMiddleware(BaseMiddleware):
    def __init__(self, config: Config):
        self.config = config

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Получить пользователя из события
        if isinstance(event, Message):
            user = event.from_user
            text = event.text or ""
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            text = ""
        else:
            return await handler(event, data)

        if not user:
            return await handler(event, data)

        # /start доступен всем — но регистрация только для whitelist
        if text.startswith("/start"):
            return await handler(event, data)

        # Проверить whitelist
        if not await db.is_whitelisted(user.id):
            if isinstance(event, Message):
                await event.answer(
                    "🔒 Доступ ограничен. Обратитесь к администратору."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("🔒 Доступ ограничен.", show_alert=True)
            return

        # Загрузить профиль пользователя в data
        db_user = await db.get_user(user.id)
        data["db_user"] = db_user
        data["config"] = self.config

        return await handler(event, data)
