import json
from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from loguru import logger

from config import Config
from utils.age_calc import calculate_age
from kb.rag_engine import search_kb, format_chunks_for_prompt
from services.claude_client import ask_claude
from services.brave_search import search_brave
from services.transcribe import transcribe_voice
from services.onboarding import should_prompt, pick_onboarding_action, mark_question_asked
from utils.text_helpers import split_long_message
from utils.thinking import ThinkingIndicator
from keyboards.main_kb import review_keyboard, onboarding_skip_keyboard, onboarding_options_keyboard
from states.fsm import OnboardingPrompt
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
    """Формирует контекст о ребёнке для system prompt (без служебных полей)."""
    from services.onboarding import format_child_context_for_llm
    return format_child_context_for_llm(db_user)


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
        reply_markup=main_menu(new_mode, _get_gender(db_user), db_user.get("role", "") if db_user else ""),
    )


# ─── Кнопка "💬 Задать вопрос" ────────────────────────────────────────────────

@router.message(F.text == "💬 Задать вопрос")
async def ask_question_prompt(message: Message):
    await message.answer("✏️ Напиши свой вопрос — я отвечу на основе базы знаний.")


# ─── Голосовые сообщения ──────────────────────────────────────────────────────

@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot, state: FSMContext, config: Config = None, db_user: dict = None):
    """Транскрибирует голосовое сообщение и обрабатывает как текст."""
    if not config or not config.deepgram_api_key:
        await message.answer("⚠️ Распознавание голоса не настроено.")
        return

    if not db_user:
        await message.answer("Сначала пройди настройку через /start")
        return

    # Скачиваем аудио
    file = await bot.get_file(message.voice.file_id)
    audio_data = await bot.download_file(file.file_path)
    audio_bytes = audio_data.read()

    # Транскрибируем
    text = await transcribe_voice(audio_bytes, config.deepgram_api_key)
    if not text:
        await message.answer("🎤 Не удалось распознать голосовое сообщение. Попробуй ещё раз или напиши текстом.")
        return

    # Показываем юзеру что распознали
    await message.answer(f"🎤 <i>{text}</i>")

    # Создаём копию сообщения с текстом (Message — frozen pydantic model)
    text_message = message.model_copy(update={"text": text})
    await handle_chat(text_message, bot, state, config, db_user)


# ─── Основной чат ─────────────────────────────────────────────────────────────

@router.message(F.text & ~F.text.startswith("/"))
async def handle_chat(message: Message, bot: Bot, state: FSMContext, config: Config = None, db_user: dict = None):
    # Не перехватываем если юзер отвечает на onboarding-вопрос
    current_state = await state.get_state()
    if current_state and current_state.startswith("OnboardingPrompt:"):
        return

    if not db_user:
        await message.answer("Сначала пройди настройку через /start")
        return

    if message.text and message.text.startswith("👶 Расскажи о"):
        return
    skip_texts = {
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

    # Onboarding prompt — после основного ответа
    await _maybe_onboarding_prompt(message, state, db_user, age)


async def _maybe_onboarding_prompt(
    message: Message, state: FSMContext, db_user: dict, age
):
    """Проверяет нужно ли показать onboarding-вопрос после ответа."""
    if not age or not db_user:
        return
    if not should_prompt(db_user):
        return

    action = await pick_onboarding_action(db_user, age.months)
    if not action:
        # Review пропущен (нет устаревших) — сбрасываем тип,
        # чтобы следующий промпт был fill
        context = {}
        if db_user.get("child_context"):
            try:
                context = json.loads(db_user["child_context"])
            except Exception:
                pass
        if context.get("_last_prompt_type") == "fill":
            context["_last_prompt_type"] = "review"
            await db.set_child_context(message.from_user.id, context)
            await db.set_last_onboarding_prompt(message.from_user.id)
        return

    # Сохраняем тип промпта в child_context для чередования fill/review
    context = {}
    if db_user.get("child_context"):
        try:
            context = json.loads(db_user["child_context"])
        except Exception:
            pass
    context["_last_prompt_type"] = action["type"]
    await db.set_child_context(message.from_user.id, context)

    if action["type"] == "fill":
        text = f"{action['disclaimer']}\n\n{action['question']}"
        options = action.get("options")
        template = action.get("template", "")

        # Помечаем вопрос как заданный в истории
        if template:
            context["_asked_questions"] = context.get("_asked_questions", [])
            if template not in context["_asked_questions"]:
                context["_asked_questions"].append(template)
            await db.set_child_context(message.from_user.id, context)

        await state.set_state(OnboardingPrompt.waiting_fill_answer)
        await state.update_data(
            onboarding_field=action["field"],
            onboarding_question=action["question"],
            onboarding_options=options,
            onboarding_template=template,
        )
        if options:
            # Добавляем подсказки к вопросу
            hints = "\n".join(f"  <b>{label}</b> — {hint}" for _, label, hint in options)
            text += f"\n\n{hints}"
            kb = onboarding_options_keyboard(options)
        else:
            kb = onboarding_skip_keyboard()
        await message.answer(text, reply_markup=kb)
        await db.set_last_onboarding_prompt(message.from_user.id)

    elif action["type"] == "review":
        text = (
            f"У меня записано про <b>{action['label'].lower()}</b>:\n"
            f"<i>«{action['value']}»</i>\n"
            f"(от {action['date_str']})\n\n"
            "Всё ещё актуально?"
        )
        await message.answer(text, reply_markup=review_keyboard(action["field"]))
        await db.set_last_onboarding_prompt(message.from_user.id)


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
