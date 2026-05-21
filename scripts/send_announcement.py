"""Отправить готовый анонс админу с кнопкой 'Отправить всем'.

Текст анонса читается из аргумента --file (или из stdin).
Сохраняется в data/announcements/<id>.txt, админу шлётся превью.
Когда админ жмёт 📢 — handler announce:send:<id> рассылает всем активным юзерам.
"""
import argparse
import asyncio
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from config import load_config
from keyboards.main_kb import announce_keyboard


async def main(text_path: str | None):
    config = load_config()

    if text_path:
        text = Path(text_path).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        logger.error("Пустой текст анонса")
        return

    announce_id = uuid.uuid4().hex[:12]
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", announce_id)[:64]
    out_dir = Path(config.data_dir).parent / "data" / "announcements"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{safe_id}.txt"
    out_file.write_text(text, encoding="utf-8")
    logger.info("Анонс сохранён: {}", out_file)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    preview = (
        "📢 <b>Готов к рассылке</b>\n"
        "Превью того, что увидят юзеры ↓\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{text}"
    )
    await bot.send_message(
        config.admin_telegram_id,
        preview,
        reply_markup=announce_keyboard(safe_id),
    )
    logger.info("Превью отправлено админу {}", config.admin_telegram_id)
    await bot.session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", "-f", help="путь к .txt с текстом")
    args = parser.parse_args()
    asyncio.run(main(args.file))
