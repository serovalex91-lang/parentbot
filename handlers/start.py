import json
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram import Bot
from loguru import logger

from config import Config
from states.fsm import Onboarding, SetDate, EditProfile, OnboardingPrompt
from keyboards.main_kb import main_menu, role_keyboard, profile_keyboard, gender_keyboard, style_keyboard, access_request_keyboard, onboarding_skip_keyboard, onboarding_options_keyboard
from utils.age_calc import parse_birthdate, calculate_age
from services.onboarding import update_context_field, remove_context_field, format_child_summary, get_fill_question, mark_question_asked
from services.claude_client import validate_onboarding_answer
import db.queries as db

router = Router()

STYLE_NAMES = {
    "gentle": "🤗 Мягкий, с сопереживанием",
    "balanced": "⚖️ Сбалансированный",
    "structured": "📏 Чёткий, с границами",
}


def _get_child_gender(db_user: dict) -> str:
    if not db_user or not db_user.get("child_context"):
        return ""
    try:
        ctx = json.loads(db_user["child_context"])
        return ctx.get("child_gender", "")
    except Exception:
        return ""


def _get_context(db_user: dict) -> dict:
    if not db_user or not db_user.get("child_context"):
        return {}
    try:
        return json.loads(db_user["child_context"])
    except Exception:
        return {}


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot, config: Config = None):
    user = message.from_user
    logger.info("/start от user.id={} username={}", user.id, user.username)

    if not await db.is_whitelisted(user.id):
        # Проверяем, был ли уже запрос
        existing = await db.get_access_request(user.id)
        if existing and existing["status"] == "pending":
            await message.answer(
                "⏳ Твой запрос уже отправлен администратору.\n"
                "Жди ответа — тебе придёт уведомление."
            )
            return

        if existing and existing["status"] == "rejected":
            await message.answer(
                "❌ Твой запрос был отклонён.\n"
                "Обратись к администратору лично, если считаешь это ошибкой."
            )
            return

        # Новый запрос
        await db.create_access_request(
            user.id, user.username or "", user.full_name or ""
        )
        await message.answer(
            "🔒 Доступ ограничен.\n\n"
            "Этот бот работает по приглашению.\n"
            "Твой запрос отправлен администратору — жди ответа."
        )
        # Уведомляем админа
        if config and config.admin_telegram_id:
            username_str = f"@{user.username}" if user.username else "нет username"
            try:
                await bot.send_message(
                    config.admin_telegram_id,
                    f"🔔 <b>Запрос на доступ</b>\n\n"
                    f"Имя: <b>{user.full_name or '—'}</b>\n"
                    f"Username: {username_str}\n"
                    f"ID: <code>{user.id}</code>",
                    reply_markup=access_request_keyboard(user.id),
                )
            except Exception as e:
                logger.error("Не удалось уведомить админа о запросе доступа: {}", e)
        return

    await db.upsert_user(user.id, user.username or "", user.full_name or "")

    if config and user.id == config.admin_telegram_id:
        _db = await db.get_db()
        await _db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user.id,))
        await _db.commit()

    db_user = await db.get_user(user.id)

    if db_user and db_user.get("onboarded_at") and db_user.get("role"):
        await state.clear()
        gender = _get_child_gender(db_user)
        await message.answer(
            f"👋 С возвращением, {user.first_name}!\n\n"
            "Выбери действие из меню ниже.",
            reply_markup=main_menu(db_user.get("search_mode", "kb_only"), gender, db_user.get("role", "")),
        )
        return

    await state.set_state(Onboarding.waiting_role)
    await message.answer(
        f"👋 Привет, {user.first_name}!\n\n"
        "Я — твой персональный эксперт по воспитанию ребёнка, основанный на "
        "доказательной педагогике и теории привязанности.\n\n"
        "Для начала: кем ты являешься?",
        reply_markup=role_keyboard(),
    )


@router.callback_query(Onboarding.waiting_role, F.data.startswith("role:"))
async def process_role(callback: CallbackQuery, state: FSMContext):
    role = callback.data.split(":")[1]
    role_names = {
        "papa": "Папа 👨", "mama": "Мама 👩", "both": "Оба родителя 👫",
        "grandpa": "Дедушка 👴", "grandma": "Бабушка 👵", "relative": "Родственник 👪",
    }
    db_user = await db.get_user(callback.from_user.id)
    old_role = db_user.get("role") if db_user else None

    await db.set_user_role(callback.from_user.id, role)

    db_user = await db.get_user(callback.from_user.id)
    if db_user and db_user.get("onboarded_at"):
        # При смене роли очищаем историю чата, чтобы Claude не путал контекст
        if old_role and old_role != role:
            await db.clear_messages(callback.from_user.id)
        await state.clear()
        await callback.message.edit_text(
            f"✅ Роль изменена: <b>{role_names.get(role, role)}</b>"
        )
        await callback.answer()
        return

    await state.set_state(Onboarding.waiting_birthdate)
    await callback.message.edit_text(
        f"Отлично, {role_names.get(role, role)}!\n\n"
        "Теперь введи дату рождения ребёнка.\n"
        "Формат: <b>20.11.2024</b> или <b>2024-11-20</b>"
    )
    await callback.answer()


@router.message(Onboarding.waiting_birthdate)
async def process_birthdate(message: Message, state: FSMContext):
    birthdate = parse_birthdate(message.text or "")
    if not birthdate:
        await message.answer(
            "❌ Не могу распознать дату. Попробуй ещё раз.\n"
            "Формат: <b>20.11.2024</b> или <b>2024-11-20</b>\n"
            "Возраст ребёнка должен быть от 0 до 18 лет."
        )
        return

    await db.set_user_birthdate(message.from_user.id, birthdate)
    age = calculate_age(birthdate)

    db_user = await db.get_user(message.from_user.id)
    await state.clear()

    age_text = f"Возраст: <b>{age.display}</b> — {age.context}" if age else ""
    gender = _get_child_gender(db_user) if db_user else ""
    await message.answer(
        f"✅ Всё готово!\n\n"
        f"👶 {age_text}\n\n"
        "Теперь ты можешь задавать вопросы, загружать книги в базу знаний "
        "и получать рекомендации, адаптированные под возраст твоего ребёнка.\n\n"
        "💡 <i>Совет: нажми «👤 Мой профиль» чтобы добавить имя и особенности ребёнка — "
        "это сделает ответы ещё точнее.</i>",
        reply_markup=main_menu(
            db_user.get("search_mode", "kb_only") if db_user else "kb_only",
            gender,
            db_user.get("role", "") if db_user else "",
        ),
    )


# ─── /fillprofile — ручной онбординг ─────────────────────────────────────────

@router.message(Command("fillprofile"))
async def cmd_fillprofile(message: Message, state: FSMContext, db_user: dict = None):
    if not db_user:
        await message.answer("Сначала пройди настройку через /start")
        return

    if not db_user.get("child_birthdate"):
        await message.answer("Сначала укажи дату рождения ребёнка через /setdate")
        return

    age = calculate_age(db_user["child_birthdate"])
    if not age:
        await message.answer("Не могу рассчитать возраст. Проверь дату через /setdate")
        return

    result = await get_fill_question(db_user, age.months)
    if not result:
        ctx = _get_context(db_user)
        cname = ctx.get("child_name", "ребёнка")
        await message.answer(
            f"На данный момент все вопросы про {cname} заданы — профиль заполнен.\n"
            f"Со временем появятся новые вопросы по мере взросления :)"
        )
        return

    field, q_text, disclaimer, options, template = result

    # Сразу помечаем вопрос как заданный в истории
    context = _get_context(db_user)
    context = mark_question_asked(context, template)
    await db.set_child_context(message.from_user.id, context)

    await state.set_state(OnboardingPrompt.waiting_fill_answer)
    await state.update_data(
        onboarding_field=field,
        onboarding_question=q_text,
        onboarding_options=options,
        onboarding_template=template,
        manual_mode=True,
        asked_questions=[q_text],
        questions_answered=0,
    )
    if options:
        hints = "\n".join(f"  <b>{label}</b> — {hint}" for _, label, hint in options)
        text = (
            f"{disclaimer}\n\n{q_text}\n\n{hints}\n\n"
            "<i>Выбери вариант или напиши свой. "
            "«Пропустить» — остановить.</i>"
        )
        kb = onboarding_options_keyboard(options)
    else:
        text = (
            f"{disclaimer}\n\n{q_text}\n\n"
            "<i>Отвечай на вопросы — я буду задавать следующий. "
            "Нажми «Пропустить» когда захочешь остановиться.</i>"
        )
        kb = onboarding_skip_keyboard()
    await message.answer(text, reply_markup=kb)


# ─── /setdate ─────────────────────────────────────────────────────────────────

@router.message(Command("setdate"))
async def cmd_setdate(message: Message, state: FSMContext):
    await state.set_state(SetDate.waiting_birthdate)
    await message.answer(
        "📅 Введи новую дату рождения ребёнка.\n"
        "Формат: <b>20.11.2024</b> или <b>2024-11-20</b>"
    )


@router.message(SetDate.waiting_birthdate)
async def process_setdate(message: Message, state: FSMContext, db_user: dict = None):
    birthdate = parse_birthdate(message.text or "")
    if not birthdate:
        await message.answer(
            "❌ Не могу распознать дату. Попробуй ещё раз.\n"
            "Формат: <b>20.11.2024</b>"
        )
        return
    await db.set_user_birthdate(message.from_user.id, birthdate)
    age = calculate_age(birthdate)
    await state.clear()
    if age:
        age_text = f"Новый возраст: <b>{age.display}</b>\n📖 {age.context}"
    else:
        age_text = ""
    db_user = await db.get_user(message.from_user.id)
    gender = _get_child_gender(db_user) if db_user else ""
    search_mode = db_user.get("search_mode", "kb_only") if db_user else "kb_only"
    await message.answer(
        f"✅ Дата рождения обновлена. {age_text}",
        reply_markup=main_menu(search_mode, gender, db_user.get("role", "") if db_user else ""),
    )


# ─── Мои расходы ─────────────────────────────────────────────────────────────

@router.message(F.text == "💰 Мои расходы")
async def cmd_my_usage(message: Message, db_user: dict = None):
    if not db_user:
        await message.answer("Сначала пройди настройку через /start")
        return

    stats = await db.get_user_usage_stats(message.from_user.id)
    total_tokens = stats["total_input"] + stats["total_output"]

    await message.answer(
        "<b>💰 Мои расходы</b>\n\n"
        f"Запросов: <b>{stats['total_requests']}</b>\n"
        f"Токенов: <b>{total_tokens:,}</b> "
        f"(вход: {stats['total_input']:,} / выход: {stats['total_output']:,})\n"
        f"Потрачено: <b>${stats['total_cost']:.4f}</b>"
    )


# ─── /myprofile ───────────────────────────────────────────────────────────────

@router.message(F.text == "👤 Мой профиль")
@router.message(Command("myprofile"))
async def cmd_myprofile(message: Message, db_user: dict = None):
    if not db_user:
        await message.answer("Сначала пройди настройку через /start")
        return

    context = _get_context(db_user)

    birthdate = db_user.get("child_birthdate", "")
    age = calculate_age(birthdate) if birthdate else None
    age_text = f"{age.display} — {age.context}" if age else "не указана"

    role_names = {
        "papa": "Папа 👨", "mama": "Мама 👩", "both": "Оба 👫",
        "grandpa": "Дедушка 👴", "grandma": "Бабушка 👵", "relative": "Родственник 👪",
    }
    role_text = role_names.get(db_user.get("role", ""), "не указана")

    gender_map = {"boy": "👦 Мальчик", "girl": "👧 Девочка"}
    gender_text = gender_map.get(context.get("child_gender", ""), "—")

    # Стиль для меня
    my_style = context.get("my_style", "")
    if my_style in STYLE_NAMES:
        my_style_text = STYLE_NAMES[my_style]
    elif my_style:
        my_style_text = f"✏️ {my_style}"
    else:
        my_style_text = "—"

    # Стиль для партнёра
    partner_style = context.get("partner_style", "")
    if partner_style in STYLE_NAMES:
        partner_style_text = STYLE_NAMES[partner_style]
    elif partner_style:
        partner_style_text = f"✏️ {partner_style}"
    else:
        partner_style_text = "—"

    text = (
        "<b>👤 Мой профиль</b>\n\n"
        f"Роль: {role_text}\n"
        f"Возраст ребёнка: <b>{age_text}</b>\n\n"
        f"⚧ Пол: {gender_text}\n"
        f"👶 Имя: {context.get('child_name', '—')}\n"
        f"⚠️ Особенности: {context.get('child_features', '—')}\n"
        f"🌟 Характер: {context.get('child_character', '—')}\n"
        f"📝 Заметки: {context.get('child_notes', '—')}\n\n"
        f"🎨 Стиль для меня: {my_style_text}\n"
        f"💬 Стиль для партнёра: {partner_style_text}\n\n"
        "<i>Нажми кнопку чтобы изменить поле:</i>"
    )
    await message.answer(text, reply_markup=profile_keyboard())


@router.callback_query(F.data.startswith("profile_edit:"))
async def profile_edit_start(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split(":")[1]

    if field == "role":
        await state.set_state(Onboarding.waiting_role)
        await callback.message.answer("👤 Выбери новую роль:", reply_markup=role_keyboard())
        await callback.answer()
        return

    if field == "child_gender":
        await callback.message.answer("⚧ Выбери пол ребёнка:", reply_markup=gender_keyboard())
        await callback.answer()
        return

    if field == "child_birthdate":
        await state.set_state(SetDate.waiting_birthdate)
        await callback.message.answer(
            "📅 Введи новую дату рождения ребёнка.\nФормат: <b>20.11.2024</b>"
        )
        await callback.answer()
        return

    if field == "my_style":
        await callback.message.answer(
            "🎨 <b>Стиль для меня</b>\n\n"
            "Как бот должен общаться <b>с тобой</b>?",
            reply_markup=style_keyboard("my"),
        )
        await callback.answer()
        return

    if field == "partner_style":
        await callback.message.answer(
            "💬 <b>Стиль для партнёра</b>\n\n"
            "Как бот должен советовать <b>общаться с партнёром</b>?",
            reply_markup=style_keyboard("partner"),
        )
        await callback.answer()
        return

    field_names = {
        "child_name": "имя ребёнка",
        "child_features": "особенности (аллергии, особые потребности)",
        "child_character": "характер ребёнка",
        "child_notes": "дополнительные заметки",
    }
    await state.set_state(EditProfile.waiting_value)
    await state.update_data(field=field)
    await callback.message.answer(
        f"✏️ Введи {field_names.get(field, field)}:\n"
        "<i>(или напиши «-» чтобы очистить)</i>"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("style:"))
async def process_style(callback: CallbackQuery, state: FSMContext, db_user: dict = None):
    # style:my:gentle или style:partner:custom
    parts = callback.data.split(":")
    target = parts[1]   # "my" или "partner"
    value = parts[2]    # "gentle", "balanced", "structured", "custom"

    field = "my_style" if target == "my" else "partner_style"
    label = "Стиль для меня" if target == "my" else "Стиль для партнёра"

    if value == "custom":
        await state.set_state(EditProfile.waiting_value)
        await state.update_data(field=field)
        if target == "my":
            await callback.message.answer(
                "✏️ Опиши как бот должен общаться <b>с тобой</b>.\n"
                "Например: <i>«чётко, по шагам, без воды, как коуч»</i>"
            )
        else:
            await callback.message.answer(
                "✏️ Опиши как бот должен советовать <b>общаться с партнёром</b>.\n"
                "Например: <i>«мягко, с сопереживанием, без назиданий»</i>"
            )
        await callback.answer()
        return

    context = _get_context(db_user)
    context[field] = value
    await db.set_child_context(callback.from_user.id, context)
    await callback.message.edit_text(
        f"✅ {label}: <b>{STYLE_NAMES.get(value, value)}</b>"
    )
    await callback.answer()


@router.message(EditProfile.waiting_value)
async def profile_edit_save(message: Message, state: FSMContext, db_user: dict = None):
    data = await state.get_data()
    field = data.get("field")
    value = message.text.strip() if message.text else ""

    context = _get_context(db_user)

    if value == "-":
        context = remove_context_field(context, field)
    else:
        # Для полей с ревизией — ставим timestamp
        if field in ("child_features", "child_character", "child_notes"):
            context = update_context_field(context, field, value)
        else:
            context[field] = value

    await db.set_child_context(message.from_user.id, context)
    await state.clear()
    await message.answer("✅ Профиль обновлён!")


@router.callback_query(F.data.startswith("gender:"))
async def process_gender(callback: CallbackQuery, db_user: dict = None):
    gender = callback.data.split(":")[1]
    gender_names = {"boy": "👦 Мальчик", "girl": "👧 Девочка"}
    context = _get_context(db_user)
    context["child_gender"] = gender
    await db.set_child_context(callback.from_user.id, context)
    await callback.message.edit_text(f"✅ Пол ребёнка: <b>{gender_names.get(gender, gender)}</b>")
    # Обновляем главное меню с новым полом
    search_mode = db_user.get("search_mode", "kb_only") if db_user else "kb_only"
    await callback.message.answer(
        "Меню обновлено 👇",
        reply_markup=main_menu(search_mode, gender, db_user.get("role", "") if db_user else ""),
    )
    await callback.answer()


# ─── Onboarding: кнопка "Пропустить" ─────────────────────────────────────────

@router.callback_query(F.data == "onboarding:skip")
async def onboarding_skip(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    manual_mode = data.get("manual_mode", False)
    answered = data.get("questions_answered", 0)
    await state.clear()
    if manual_mode and answered > 0:
        await callback.message.edit_text(
            f"Онбординг завершён — ответов: <b>{answered}</b>. "
            f"Профиль стал полнее, буду учитывать в ответах :)"
        )
    else:
        await callback.message.edit_text("Пропустим пока :)")
    await callback.answer()


# ─── Onboarding: выбор варианта ответа ─────────────────────────────────────────

@router.callback_query(F.data.startswith("onb_opt:"))
async def onboarding_option_selected(callback: CallbackQuery, state: FSMContext, db_user: dict = None):
    data = await state.get_data()
    field = data.get("onboarding_field")
    if not field:
        await callback.answer("Сессия устарела")
        return

    choice = callback.data.split(":", 1)[1]

    if choice == "custom":
        # Переключаем на текстовый ввод — убираем кнопки, просим написать
        await state.update_data(onboarding_options=None)
        await callback.message.edit_text(
            f"{callback.message.text}\n\n<b>Напиши свой вариант:</b>"
        )
        await callback.answer()
        return

    # Выбран один из вариантов
    options = data.get("onboarding_options", [])
    try:
        idx = int(choice)
        value, label, hint = options[idx]
    except (ValueError, IndexError):
        await callback.answer("Ошибка выбора")
        return

    # Сохраняем выбранное значение (без дубликатов)
    context = _get_context(db_user)
    existing = context.get(field, "")
    if existing:
        final_value = f"{existing}; {value}"
    else:
        final_value = value
    question = data.get("onboarding_question", "")
    context = update_context_field(context, field, final_value, question=question)
    await db.set_child_context(callback.from_user.id, context)

    field_labels = {
        "child_features": "особенности",
        "child_character": "характер",
        "child_notes": "заметки",
    }
    flabel = field_labels.get(field, field)

    # Ручной онбординг — сразу следующий вопрос
    manual_mode = data.get("manual_mode", False)
    if manual_mode:
        asked = data.get("asked_questions", [])
        answered = data.get("questions_answered", 0) + 1

        db_user_fresh = await db.get_user(callback.from_user.id)
        birthdate = db_user_fresh.get("child_birthdate", "") if db_user_fresh else ""
        age_fresh = calculate_age(birthdate) if birthdate else None

        next_q = None
        if age_fresh:
            next_q = await get_fill_question(
                db_user_fresh, age_fresh.months, exclude_questions=set(asked)
            )

        if next_q:
            next_field, next_text, next_disclaimer, next_options, next_template = next_q
            asked.append(next_template)

            # Помечаем вопрос как заданный
            ctx_fresh = _get_context(db_user_fresh) if db_user_fresh else {}
            ctx_fresh = mark_question_asked(ctx_fresh, next_template)
            await db.set_child_context(callback.from_user.id, ctx_fresh)

            await state.set_state(OnboardingPrompt.waiting_fill_answer)
            await state.update_data(
                onboarding_field=next_field,
                onboarding_question=next_text,
                onboarding_options=next_options,
                onboarding_template=next_template,
                manual_mode=True,
                asked_questions=asked,
                questions_answered=answered,
            )
            if next_options:
                hints = "\n".join(
                    f"  <b>{ol}</b> — {oh}" for _, ol, oh in next_options
                )
                await callback.message.edit_text(
                    f"Записано (<b>{flabel}</b>): <i>{value}</i>\n\n"
                    f"{next_disclaimer}\n\n{next_text}\n\n{hints}",
                    reply_markup=onboarding_options_keyboard(next_options),
                )
            else:
                await callback.message.edit_text(
                    f"Записано (<b>{flabel}</b>): <i>{value}</i>\n\n"
                    f"{next_disclaimer}\n\n{next_text}",
                    reply_markup=onboarding_skip_keyboard(),
                )
            await callback.answer()
            return
        else:
            await callback.message.edit_text(
                f"Записано (<b>{flabel}</b>): <i>{value}</i>\n\n"
                f"Все вопросы на этот возраст пройдены — ответов: <b>{answered}</b>.\n"
                f"Профиль стал полнее, буду учитывать в ответах :)"
            )
            await state.clear()
            await callback.answer()
            return

    # Авто-онбординг — одиночный вопрос
    await state.clear()
    await callback.message.edit_text(
        f"Записано в профиль (<b>{flabel}</b>): <i>{value}</i>\n\n"
        f"Буду учитывать в ответах :)"
    )
    await callback.answer()


# ─── Onboarding: ответ на fill-вопрос ─────────────────────────────────────────

@router.message(OnboardingPrompt.waiting_fill_answer)
async def onboarding_fill_answer(message: Message, state: FSMContext, db_user: dict = None):
    data = await state.get_data()
    field = data.get("onboarding_field")
    question = data.get("onboarding_question", "")
    value = (message.text or "").strip()

    if not value or not field:
        await state.clear()
        await message.answer("Пропустим пока :)")
        return

    # Фильтрация команд — не сохранять /admin, /start и т.д. как ответы
    if value.startswith("/"):
        await state.clear()
        await message.answer("Это похоже на команду, а не на ответ. Пропускаю вопрос.")
        return

    context = _get_context(db_user)
    child_name = context.get("child_name", "ребёнка")

    # Определяем возраст для валидации
    birthdate = db_user.get("child_birthdate", "") if db_user else ""
    age = calculate_age(birthdate) if birthdate else None
    age_months = age.months if age else 12

    # Валидация через Haiku (state НЕ очищаем до результата — защита от race condition)
    result = await validate_onboarding_answer(question, value, age_months, field)

    # Записываем стоимость валидации
    if result.cost_usd > 0:
        await db.add_token_usage(
            user_id=message.from_user.id,
            model="claude-haiku-4-5-20251001",
            input_tokens=0, output_tokens=0,
            cost_usd=result.cost_usd,
        )

    if result.reason == "skip":
        await state.clear()
        await message.answer("Ок, пропустим :)")
        return

    if not result.is_valid:
        # Оставляем state — юзер ещё отвечает
        await message.answer(
            f"Хм, кажется что-то не так: <i>{result.reason}</i>\n"
            "Попробуй ещё раз или напиши «пропустить»."
        )
        return

    # Ответ валидный — теперь очищаем state
    await state.clear()
    normalized = result.normalized

    # Если поле уже заполнено — дополняем (без дубликатов)
    existing = context.get(field, "")
    if existing and normalized.lower() in existing.lower():
        final_value = existing  # уже есть
    elif existing:
        final_value = f"{existing}; {normalized}"
    else:
        final_value = normalized

    context = update_context_field(context, field, final_value, question=question)
    await db.set_child_context(message.from_user.id, context)

    field_labels = {
        "child_features": "особенности",
        "child_character": "характер",
        "child_notes": "заметки",
    }
    label = field_labels.get(field, field)

    # Ручной онбординг — сразу задаём следующий вопрос
    manual_mode = data.get("manual_mode", False)
    if manual_mode:
        asked = data.get("asked_questions", [])
        answered = data.get("questions_answered", 0) + 1

        # Перечитываем юзера из БД — контекст обновился
        db_user_fresh = await db.get_user(message.from_user.id)
        birthdate = db_user_fresh.get("child_birthdate", "") if db_user_fresh else ""
        age_fresh = calculate_age(birthdate) if birthdate else None

        next_q = None
        if age_fresh:
            next_q = await get_fill_question(
                db_user_fresh, age_fresh.months, exclude_questions=set(asked)
            )

        if next_q:
            next_field, next_text, next_disclaimer, next_options, next_template = next_q
            asked.append(next_template)

            # Помечаем вопрос как заданный
            ctx_fresh = _get_context(db_user_fresh) if db_user_fresh else {}
            ctx_fresh = mark_question_asked(ctx_fresh, next_template)
            await db.set_child_context(message.from_user.id, ctx_fresh)

            await state.set_state(OnboardingPrompt.waiting_fill_answer)
            await state.update_data(
                onboarding_field=next_field,
                onboarding_question=next_text,
                onboarding_options=next_options,
                onboarding_template=next_template,
                manual_mode=True,
                asked_questions=asked,
                questions_answered=answered,
            )
            if next_options:
                hints = "\n".join(
                    f"  <b>{ol}</b> — {oh}" for _, ol, oh in next_options
                )
                await message.answer(
                    f"Записано (<b>{label}</b>): <i>{normalized}</i>\n\n"
                    f"{next_disclaimer}\n\n{next_text}\n\n{hints}",
                    reply_markup=onboarding_options_keyboard(next_options),
                )
            else:
                await message.answer(
                    f"Записано (<b>{label}</b>): <i>{normalized}</i>\n\n"
                    f"{next_disclaimer}\n\n{next_text}",
                    reply_markup=onboarding_skip_keyboard(),
                )
            return
        else:
            # Вопросы закончились
            await state.clear()
            await message.answer(
                f"Записано (<b>{label}</b>): <i>{normalized}</i>\n\n"
                f"Все вопросы на этот возраст пройдены — ответов: <b>{answered}</b>.\n"
                f"Профиль стал полнее, буду учитывать в ответах :)"
            )
            return

    await message.answer(
        f"Записано в профиль {child_name} (<b>{label}</b>):\n"
        f"<i>{normalized}</i>\n\n"
        f"Буду учитывать в ответах :)"
    )


# ─── Onboarding: ревизия (review) ───────────────────────────────────────────

@router.callback_query(F.data.startswith("review:"))
async def process_review(callback: CallbackQuery, state: FSMContext, db_user: dict = None):
    if not db_user:
        await callback.answer("Сначала пройди настройку")
        return

    parts = callback.data.split(":")
    action = parts[1]   # keep, edit, delete
    field = parts[2]

    context = _get_context(db_user)
    child_name = context.get("child_name", "ребёнка")

    if action == "keep":
        # Обновляем только timestamp
        context = update_context_field(context, field, context.get(field, ""))
        await db.set_child_context(callback.from_user.id, context)
        await callback.message.edit_text("Отлично, оставляю как есть.")
        await callback.answer()

    elif action == "delete":
        context = remove_context_field(context, field)
        await db.set_child_context(callback.from_user.id, context)
        await callback.message.edit_text(f"Убрала из профиля {child_name}.")
        await callback.answer()

    elif action == "edit":
        await state.set_state(OnboardingPrompt.waiting_review_edit)
        await state.update_data(onboarding_field=field)
        await callback.message.edit_text("Напиши новое значение:")
        await callback.answer()


@router.message(OnboardingPrompt.waiting_review_edit)
async def onboarding_review_edit(message: Message, state: FSMContext, db_user: dict = None):
    data = await state.get_data()
    field = data.get("onboarding_field")
    value = (message.text or "").strip()

    if not value or not field:
        await state.clear()
        await message.answer("Оставляю без изменений.")
        return

    # Фильтрация команд
    if value.startswith("/"):
        await state.clear()
        await message.answer("Это похоже на команду, а не на ответ. Оставляю без изменений.")
        return

    context = _get_context(db_user)
    child_name = context.get("child_name", "ребёнка")

    # Определяем возраст для валидации
    birthdate = db_user.get("child_birthdate", "") if db_user else ""
    age = calculate_age(birthdate) if birthdate else None
    age_months = age.months if age else 12

    # Валидация через Haiku (state НЕ очищаем до результата)
    result = await validate_onboarding_answer(
        "Обновление информации", value, age_months, field
    )

    if result.cost_usd > 0:
        await db.add_token_usage(
            user_id=message.from_user.id,
            model="claude-haiku-4-5-20251001",
            input_tokens=0, output_tokens=0,
            cost_usd=result.cost_usd,
        )

    if result.reason == "skip":
        await state.clear()
        await message.answer("Оставляю без изменений.")
        return

    if not result.is_valid:
        # Оставляем state — юзер ещё отвечает
        await message.answer(
            f"Хм, кажется что-то не так: <i>{result.reason}</i>\n"
            "Попробуй ещё раз или напиши «пропустить»."
        )
        return

    await state.clear()
    normalized = result.normalized
    context = update_context_field(context, field, normalized)
    await db.set_child_context(message.from_user.id, context)

    field_labels = {
        "child_features": "особенности",
        "child_character": "характер",
        "child_notes": "заметки",
    }
    label = field_labels.get(field, field)
    await message.answer(
        f"Обновлено (<b>{label}</b>) {child_name}:\n"
        f"<i>{normalized}</i>\n\n"
        f"Учту в следующих ответах :)"
    )


# ─── Сводка о ребёнке (экспорт) ─────────────────────────────────────────────

@router.callback_query(F.data == "child_summary")
async def child_summary_handler(callback: CallbackQuery, db_user: dict = None):
    if not db_user:
        await callback.answer("Сначала пройди настройку")
        return

    birthdate = db_user.get("child_birthdate", "")
    age = calculate_age(birthdate) if birthdate else None
    age_display = age.display if age else ""

    summary = format_child_summary(db_user, age_display)
    await callback.message.answer(summary)
    await callback.answer()


@router.callback_query(F.data == "start_fillprofile")
async def start_fillprofile_callback(callback: CallbackQuery, state: FSMContext, db_user: dict = None):
    """Кнопка 'Заполнить профиль' из меню профиля — запускает /fillprofile."""
    if not db_user:
        await callback.answer("Сначала пройди настройку")
        return
    # Создаём фейковое сообщение-обёртку не нужно — просто вызовем логику напрямую
    await callback.answer()
    if not db_user.get("child_birthdate"):
        await callback.message.answer("Сначала укажи дату рождения ребёнка через /setdate")
        return

    age = calculate_age(db_user["child_birthdate"])
    if not age:
        await callback.message.answer("Не могу рассчитать возраст. Проверь дату через /setdate")
        return

    result = await get_fill_question(db_user, age.months)
    if not result:
        ctx = _get_context(db_user)
        cname = ctx.get("child_name", "ребёнка")
        await callback.message.answer(
            f"На данный момент все вопросы про {cname} заданы — профиль заполнен.\n"
            f"Со временем появятся новые вопросы по мере взросления :)"
        )
        return

    field, q_text, disclaimer, options, template = result

    context = _get_context(db_user)
    context = mark_question_asked(context, template)
    await db.set_child_context(callback.from_user.id, context)

    await state.set_state(OnboardingPrompt.waiting_fill_answer)
    await state.update_data(
        onboarding_field=field,
        onboarding_question=q_text,
        onboarding_options=options,
        onboarding_template=template,
        manual_mode=True,
        asked_questions=[q_text],
        questions_answered=0,
    )
    if options:
        hints = "\n".join(f"  <b>{label}</b> — {hint}" for _, label, hint in options)
        text = (
            f"{disclaimer}\n\n{q_text}\n\n{hints}\n\n"
            "<i>Выбери вариант или напиши свой. "
            "«Пропустить» — остановить.</i>"
        )
        kb = onboarding_options_keyboard(options)
    else:
        text = (
            f"{disclaimer}\n\n{q_text}\n\n"
            "<i>Отвечай на вопросы — я буду задавать следующий. "
            "Нажми «Пропустить» когда захочешь остановиться.</i>"
        )
        kb = onboarding_skip_keyboard()
    await callback.message.answer(text, reply_markup=kb)


# ─── Запрос на доступ (approve / reject) ─────────────────────────────────────

@router.callback_query(F.data.startswith("access:"))
async def process_access_request(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    action = parts[1]       # "approve" или "reject"
    target_id = int(parts[2])

    if action == "approve":
        await db.add_to_whitelist(target_id, callback.from_user.id)
        await db.resolve_access_request(target_id, "approved")
        await callback.message.edit_text(
            callback.message.text + f"\n\n✅ <b>Добавлен в whitelist</b>",
        )
        try:
            await bot.send_message(
                target_id,
                "✅ Доступ открыт! Нажми /start чтобы начать.",
            )
        except Exception as e:
            logger.warning("Не удалось уведомить юзера {}: {}", target_id, e)
    else:
        await db.resolve_access_request(target_id, "rejected")
        await callback.message.edit_text(
            callback.message.text + f"\n\n❌ <b>Отклонён</b>",
        )
        try:
            await bot.send_message(
                target_id,
                "❌ К сожалению, запрос на доступ отклонён.",
            )
        except Exception as e:
            logger.warning("Не удалось уведомить юзера {}: {}", target_id, e)

    await callback.answer()
