import json
from aiogram import Router, F, Bot
from aiogram.types import Message
from loguru import logger

from config import Config
from utils.age_calc import calculate_age
from kb.rag_engine import search_kb, format_chunks_for_prompt
from services.claude_client import ask_claude
from services.brave_search import search_brave
from utils.text_helpers import split_long_message
from utils.thinking import ThinkingIndicator
import db.queries as db
import asyncio

router = Router()


def _get_gender(db_user: dict) -> str:
    if not db_user or not db_user.get("child_context"):
        return ""
    try:
        ctx = json.loads(db_user["child_context"])
        return ctx.get("child_gender", "")
    except Exception:
        return ""


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
        reply_markup=main_menu(new_mode, _get_gender(db_user)),
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

    skip_texts = {
        "👶 Расскажи о дочке",
        "👶 Расскажи о сыночке",
        "👶 Расскажи о ребёнке",
        "📖 Моя библиотека",
        "👤 Мой профиль",
        "❓ Помощь",
        "💬 Задать вопрос",
        "💰 Мои расходы",
    }
    if message.text in skip_texts:
        return

    user_id = message.from_user.id
    user_text = message.text.strip()

    async with ThinkingIndicator(bot, message.chat.id, "Ищу в базе знаний...") as thinking:
        # Возраст ребёнка
        birthdate = db_user.get("child_birthdate")
        age = calculate_age(birthdate) if birthdate else None
        age_display = age.display if age else "не указан"
        age_context = age.context if age else ""
        age_months = age.months if age else None

        # Параллельно: история + исключения + контекст ребёнка
        search_mode = db_user.get("search_mode", "kb_only")
        max_hist = config.max_history_messages if config else 20
        gap_hours = config.session_gap_hours if config else 4

        excluded_ids, history, child_context = await asyncio.gather(
            db.get_excluded_book_ids(user_id),
            db.get_last_messages(user_id, limit=max_hist, session_gap_hours=gap_hours),
            _get_child_context_str(db_user),
        )

        # RAG поиск (async, в отдельном потоке)
        chunks = await search_kb(
            user_id=user_id,
            query=user_text,
            age_months=age_months,
            excluded_book_ids=excluded_ids,
            n_results=5,
        )
        kb_text = format_chunks_for_prompt(chunks)

        if not chunks:
            kb_text = ""
            logger.info("RAG вернул 0 чанков для user={}, query={}", user_id, user_text[:50])

        # Brave Search
        brave_text = ""
        if search_mode == "kb_internet" and config and config.brave_api_key:
            await thinking.update("Ищу в интернете...")
            try:
                brave_text = await search_brave(config.brave_api_key, user_text) or ""
            except Exception as e:
                logger.warning("Brave Search недоступен: {}", e)
                brave_text = ""

        # Валидация истории
        validated_history = _validate_history(history)

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

        await thinking.update("Генерирую ответ...")

        # Вызов Claude
        try:
            result = await ask_claude(
                config=config,
                role=role,
                age_display=age_display,
                age_context=age_context,
                kb_chunks=kb_text,
                history=validated_history,
                user_message=user_text,
                child_context=child_context,
                brave_results=brave_text,
                my_style=my_style,
                partner_style=partner_style,
            )
        except Exception as e:
            logger.error("Ошибка Claude API для user={}: {}", user_id, e)
            await message.answer("❌ Ошибка при получении ответа. Попробуй позже.")
            return

    response_text = result.text

    # Сохранить в историю
    await db.add_message(user_id, "user", user_text)
    await db.add_message(user_id, "assistant", response_text)
    await db.prune_old_messages(user_id, keep=100)

    # Сохранить usage
    await db.add_token_usage(
        user_id=user_id,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
    )

    if brave_text and search_mode == "kb_internet":
        response_text += "\n\n<i>🌐 Ответ дополнен данными из интернета.</i>"

    # Строка стоимости
    model_short = result.model.split("-")[1].capitalize()
    cost_line = (
        f"\n\n<i>{model_short} · "
        f"{result.input_tokens + result.output_tokens} tok · "
        f"${result.cost_usd:.4f}</i>"
    )
    response_text += cost_line

    for part in split_long_message(response_text):
        await message.answer(part)


def _validate_history(history: list) -> list:
    """Убирает дублирующиеся подряд роли — Claude требует чередование."""
    if not history:
        return []
    validated = [history[0]]
    for msg in history[1:]:
        if msg["role"] != validated[-1]["role"]:
            validated.append(msg)
        else:
            validated[-1]["content"] += "\n" + msg["content"]
    return validated
