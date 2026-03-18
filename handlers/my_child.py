import json
from aiogram import Router, F, Bot
from aiogram.types import Message
from loguru import logger

from config import Config
from utils.age_calc import calculate_age
from kb.rag_engine import search_kb, format_chunks_for_prompt
from services.claude_client import ask_claude
from utils.text_helpers import split_long_message
from utils.thinking import ThinkingIndicator
import db.queries as db

router = Router()


@router.message(F.text.startswith("👶 Расскажи о"))
async def my_child_handler(message: Message, bot: Bot, config: Config = None, db_user: dict = None):
    if not db_user:
        await message.answer("Сначала пройди настройку через /start")
        return

    birthdate = db_user.get("child_birthdate")
    if not birthdate:
        await message.answer(
            "📅 Дата рождения ребёнка не указана.\n"
            "Введи её через /setdate или в 👤 Мой профиль."
        )
        return

    age = calculate_age(birthdate)
    if not age:
        await message.answer("⚠️ Не удалось вычислить возраст. Проверь дату рождения.")
        return

    user_id = message.from_user.id

    async with ThinkingIndicator(bot, message.chat.id, "Ищу информацию по возрасту...") as thinking:
        excluded_ids = await db.get_excluded_book_ids(user_id)

        query = f"Развитие ребёнка в {age.display}: ключевые этапы, потребности, советы родителям"
        chunks = await search_kb(
            user_id=user_id,
            query=query,
            age_months=age.months,
            excluded_book_ids=excluded_ids,
            n_results=5,
        )

        if not chunks:
            await message.answer(
                f"📚 В базе знаний нет книг для возраста <b>{age.display}</b>.\n\n"
                "Загрузи подходящую книгу через 📖 Моя библиотека."
            )
            return

        kb_text = format_chunks_for_prompt(chunks)

        child_context = ""
        if db_user.get("child_context"):
            try:
                ctx = json.loads(db_user["child_context"])
                parts = []
                if ctx.get("child_name"):
                    parts.append(f"Имя: {ctx['child_name']}")
                if ctx.get("child_features"):
                    parts.append(f"Особенности: {ctx['child_features']}")
                if ctx.get("child_character"):
                    parts.append(f"Характер: {ctx['child_character']}")
                if ctx.get("child_notes"):
                    parts.append(f"Заметки: {ctx['child_notes']}")
                child_context = "\n".join(parts)
            except Exception:
                pass

        user_prompt = (
            f"Расскажи мне о ключевых этапах развития ребёнка в возрасте {age.display} ({age.context}). "
            f"ВАЖНО: точный возраст ребёнка — {age.display}. Используй именно этот возраст в ответе, не округляй и не заменяй на другие формулировки. "
            "Что важно знать родителям? Какие потребности у ребёнка сейчас? "
            "Дай 2-3 конкретных совета и один маленький практический шаг."
        )

        role = db_user.get("role", "both")
        my_style = ""
        partner_style = ""
        if db_user.get("child_context"):
            try:
                ctx = json.loads(db_user["child_context"])
                my_style = ctx.get("my_style", "")
                partner_style = ctx.get("partner_style", "")
            except Exception:
                pass

        gap_hours = config.session_gap_hours if config else 4
        history = await db.get_last_messages(
            user_id,
            limit=config.max_history_messages if config else 20,
            session_gap_hours=gap_hours,
        )

        await thinking.update("Генерирую рекомендации...")

        try:
            result = await ask_claude(
                config=config,
                role=role,
                age_display=age.display,
                age_context=age.context,
                kb_chunks=kb_text,
                history=history,
                user_message=user_prompt,
                child_context=child_context,
                my_style=my_style,
                partner_style=partner_style,
            )
        except Exception as e:
            logger.error("Ошибка Claude в my_child: {}", e)
            await message.answer("❌ Ошибка при получении ответа. Попробуй позже.")
            return

    await db.add_message(user_id, "user", user_prompt)
    await db.add_message(user_id, "assistant", result.text)

    await db.add_token_usage(
        user_id=user_id,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
    )

    model_short = result.model.split("-")[1].capitalize()
    cost_line = (
        f"\n\n<i>{model_short} · "
        f"{result.input_tokens + result.output_tokens} tok · "
        f"${result.cost_usd:.4f}</i>"
    )
    response_text = result.text + cost_line

    for part in split_long_message(response_text):
        await message.answer(part)
