import json
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from loguru import logger

from config import Config
from utils.age_calc import calculate_age
from kb.rag_engine import search_kb, format_chunks_for_prompt
from services.claude_client import ask_claude
from services.brave_search import search_brave
from utils.text_helpers import split_long_message
import db.queries as db

router = Router()


async def _get_child_context_str(db_user: dict) -> str:
    if not db_user or not db_user.get("child_context"):
        return ""
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
        return "\n".join(parts)
    except Exception:
        return ""


# ─── Переключение режима поиска ───────────────────────────────────────────────

@router.message(F.text.startswith("📚 Только из книг"))
@router.message(F.text.startswith("🌐 Книги + интернет"))
async def toggle_search_mode(message: Message, db_user: dict = None):
    if not db_user:
        return
    current = db_user.get("search_mode", "kb_only")
    new_mode = "kb_internet" if current == "kb_only" else "kb_only"
    await db.set_search_mode(message.from_user.id, new_mode)

    from keyboards.main_kb import main_menu
    mode_name = "🌐 Книги + интернет" if new_mode == "kb_internet" else "📚 Только из книг"
    await message.answer(
        f"✅ Режим поиска изменён: <b>{mode_name}</b>",
        reply_markup=main_menu(new_mode),
    )


# ─── Кнопка "💬 Задать вопрос" ────────────────────────────────────────────────

@router.message(F.text == "💬 Задать вопрос")
async def ask_question_prompt(message: Message):
    await message.answer("✏️ Напиши свой вопрос — я отвечу на основе базы знаний.")


# ─── Основной чат ─────────────────────────────────────────────────────────────

@router.message(F.text & ~F.text.startswith("/"))
async def handle_chat(message: Message, bot: Bot, config: Config = None, db_user: dict = None):
    if not db_user:
        await message.answer("Сначала пройди настройку через /start")
        return

    # Пропустить служебные кнопки меню
    skip_texts = {
        "👶 Расскажи о дочке",
        "📖 Моя библиотека",
        "👤 Мой профиль",
        "❓ Помощь",
        "💬 Задать вопрос",
    }
    if message.text in skip_texts:
        return

    user_id = message.from_user.id
    user_text = message.text.strip()

    await bot.send_chat_action(message.chat.id, "typing")

    # Возраст ребёнка
    birthdate = db_user.get("child_birthdate")
    age = calculate_age(birthdate) if birthdate else None
    age_display = age.display if age else "не указан"
    age_context = age.context if age else ""
    age_months = age.months if age else None

    # Исключённые книги
    excluded_ids = await db.get_excluded_book_ids(user_id)

    # RAG поиск
    chunks = search_kb(
        user_id=user_id,
        query=user_text,
        age_months=age_months,
        excluded_book_ids=excluded_ids,
        n_results=10,
    )
    kb_text = format_chunks_for_prompt(chunks)

    if not chunks:
        kb_text = ""
        logger.info("RAG вернул 0 чанков для user={}, query={}", user_id, user_text[:50])

    # Brave Search (если режим kb_internet)
    brave_text = ""
    search_mode = db_user.get("search_mode", "kb_only")
    if search_mode == "kb_internet" and config and config.brave_api_key:
        await bot.send_chat_action(message.chat.id, "typing")
        try:
            brave_text = await search_brave(config.brave_api_key, user_text) or ""
        except Exception as e:
            logger.warning("Brave Search недоступен: {}", e)
            brave_text = ""

    # История сообщений
    max_hist = config.max_history_messages if config else 20
    history = await db.get_last_messages(user_id, limit=max_hist)

    # Персональный контекст
    child_context = await _get_child_context_str(db_user)
    role = db_user.get("role", "both")

    # Вызов Claude
    try:
        response = await ask_claude(
            config=config,
            role=role,
            age_display=age_display,
            age_context=age_context,
            kb_chunks=kb_text,
            history=history,
            user_message=user_text,
            child_context=child_context,
            brave_results=brave_text,
        )
    except Exception as e:
        logger.error("Ошибка Claude API для user={}: {}", user_id, e)
        await message.answer("❌ Ошибка при получении ответа. Попробуй позже.")
        return

    # Сохранить в историю
    await db.add_message(user_id, "user", user_text)
    await db.add_message(user_id, "assistant", response)

    # Добавить сноску если использовались интернет-источники
    if brave_text and search_mode == "kb_internet":
        response += "\n\n<i>🌐 Ответ дополнен данными из интернета.</i>"

    for part in split_long_message(response):
        await message.answer(part)
