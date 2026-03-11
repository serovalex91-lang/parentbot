import asyncio
from aiogram import Bot
from aiogram.types import Message
from loguru import logger


class ThinkingIndicator:
    """
    Визуальный индикатор обработки запроса.
    Показывает анимированное сообщение с этапами работы.
    
    Использование:
        async with ThinkingIndicator(bot, chat_id) as thinking:
            await thinking.update("Ищу в базе знаний...")
            result = await search_kb(...)
            await thinking.update("Генерирую ответ...")
            response = await ask_claude(...)
        # Сообщение-индикатор автоматически удаляется
    """

    FRAMES = ["◐", "◓", "◑", "◒"]

    def __init__(self, bot: Bot, chat_id: int, initial_text: str = "Обрабатываю запрос..."):
        self.bot = bot
        self.chat_id = chat_id
        self.text = initial_text
        self.message: Message = None
        self._task: asyncio.Task = None
        self._frame_idx = 0
        self._running = False

    async def __aenter__(self):
        self.message = await self.bot.send_message(
            self.chat_id,
            f"{self.FRAMES[0]} <i>{self.text}</i>",
        )
        self._running = True
        self._task = asyncio.create_task(self._animate())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Удаляем сообщение-индикатор
        if self.message:
            try:
                await self.message.delete()
            except Exception:
                pass
        return False

    async def update(self, text: str):
        """Обновить текст этапа."""
        self.text = text
        self._frame_idx = 0
        if self.message:
            try:
                await self.message.edit_text(
                    f"{self.FRAMES[0]} <i>{self.text}</i>"
                )
            except Exception:
                pass

    async def _animate(self):
        """Анимация — крутит символ каждые 1.5 сек + обновляет typing action."""
        while self._running:
            await asyncio.sleep(1.5)
            if not self._running:
                break
            self._frame_idx = (self._frame_idx + 1) % len(self.FRAMES)
            try:
                await self.bot.send_chat_action(self.chat_id, "typing")
                await self.message.edit_text(
                    f"{self.FRAMES[self._frame_idx]} <i>{self.text}</i>"
                )
            except Exception:
                pass
