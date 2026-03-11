import json
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from loguru import logger

from config import Config
from states.fsm import Onboarding, SetDate, EditProfile
from keyboards.main_kb import main_menu, role_keyboard, profile_keyboard, gender_keyboard, style_keyboard
from utils.age_calc import parse_birthdate, calculate_age
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
async def cmd_start(message: Message, state: FSMContext, config: Config = None):
    user = message.from_user
    logger.info("/start от user.id={} username={}", user.id, user.username)

    if not await db.is_whitelisted(user.id):
        await message.answer(
            "🔒 Доступ ограничен.\n\n"
            "Этот бот работает только для приглашённых пользователей. "
            "Обратитесь к администратору."
        )
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
            reply_markup=main_menu(db_user.get("search_mode", "kb_only"), gender),
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
    role_names = {"papa": "Папа 👨", "mama": "Мама 👩", "both": "Оба родителя 👫"}
    await db.set_user_role(callback.from_user.id, role)

    db_user = await db.get_user(callback.from_user.id)
    if db_user and db_user.get("onboarded_at"):
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
        ),
    )


# ─── /setdate ─────────────────────────────────────────────────────────────────

@router.message(Command("setdate"))
async def cmd_setdate(message: Message, state: FSMContext):
    await state.set_state(SetDate.waiting_birthdate)
    await message.answer(
        "📅 Введи новую дату рождения ребёнка.\n"
        "Формат: <b>20.11.2024</b> или <b>2024-11-20</b>"
    )


@router.message(SetDate.waiting_birthdate)
async def process_setdate(message: Message, state: FSMContext):
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
    age_text = f"Новый возраст: <b>{age.display}</b>" if age else ""
    await message.answer(f"✅ Дата рождения обновлена. {age_text}")


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
    age_text = age.display if age else "не указана"

    role_names = {"papa": "Папа 👨", "mama": "Мама 👩", "both": "Оба 👫"}
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
        context.pop(field, None)
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
    await callback.answer()
