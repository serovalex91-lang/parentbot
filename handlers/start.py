import json
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from loguru import logger

from config import Config
from states.fsm import Onboarding, SetDate, EditProfile
from keyboards.main_kb import main_menu, role_keyboard, profile_keyboard, gender_keyboard
from utils.age_calc import parse_birthdate, calculate_age
import db.queries as db

router = Router()


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, config: Config = None):
    user = message.from_user
    logger.info("/start от user.id={} username={}", user.id, user.username)

    # Проверить whitelist
    if not await db.is_whitelisted(user.id):
        await message.answer(
            "🔒 Доступ ограничен.\n\n"
            "Этот бот работает только для приглашённых пользователей. "
            "Обратитесь к администратору."
        )
        return

    # Создать/обновить запись пользователя
    await db.upsert_user(user.id, user.username or "", user.full_name or "")

    # Назначить права администратора
    if config and user.id == config.admin_telegram_id:
        async with __import__("aiosqlite").connect(db.get_db_path()) as conn:
            await conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user.id,))
            await conn.commit()

    db_user = await db.get_user(user.id)

    # Если уже прошёл онбординг — показать меню
    if db_user and db_user.get("onboarded_at") and db_user.get("role"):
        await state.clear()
        await message.answer(
            f"👋 С возвращением, {user.first_name}!\n\n"
            "Выбери действие из меню ниже.",
            reply_markup=main_menu(db_user.get("search_mode", "kb_only")),
        )
        return

    # Начать онбординг
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

    # Если пользователь уже онбордингован — просто подтвердить смену роли
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
    await message.answer(
        f"✅ Всё готово!\n\n"
        f"👶 {age_text}\n\n"
        "Теперь ты можешь задавать вопросы, загружать книги в базу знаний "
        "и получать рекомендации, адаптированные под возраст твоего ребёнка.\n\n"
        "💡 <i>Совет: нажми «👤 Мой профиль» чтобы добавить имя и особенности ребёнка — "
        "это сделает ответы ещё точнее.</i>",
        reply_markup=main_menu(db_user.get("search_mode", "kb_only") if db_user else "kb_only"),
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

    context = {}
    if db_user.get("child_context"):
        try:
            context = json.loads(db_user["child_context"])
        except Exception:
            pass

    birthdate = db_user.get("child_birthdate", "")
    age = calculate_age(birthdate) if birthdate else None
    age_text = age.display if age else "не указана"

    role_names = {"papa": "Папа 👨", "mama": "Мама 👩", "both": "Оба 👫"}
    role_text = role_names.get(db_user.get("role", ""), "не указана")

    gender_map = {"boy": "👦 Мальчик", "girl": "👧 Девочка"}
    gender_text = gender_map.get(context.get("child_gender", ""), "—")

    text = (
        "<b>👤 Мой профиль</b>\n\n"
        f"Роль: {role_text}\n"
        f"Возраст ребёнка: <b>{age_text}</b>\n\n"
        f"⚧ Пол: {gender_text}\n"
        f"👶 Имя: {context.get('child_name', '—')}\n"
        f"⚠️ Особенности: {context.get('child_features', '—')}\n"
        f"🌟 Характер: {context.get('child_character', '—')}\n"
        f"📝 Заметки: {context.get('child_notes', '—')}\n\n"
        "<i>Нажми кнопку чтобы изменить поле:</i>"
    )
    await message.answer(text, reply_markup=profile_keyboard())


@router.callback_query(F.data.startswith("profile_edit:"))
async def profile_edit_start(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split(":")[1]

    if field == "role":
        await state.set_state(Onboarding.waiting_role)
        await callback.message.answer(
            "👤 Выбери новую роль:",
            reply_markup=role_keyboard(),
        )
        await callback.answer()
        return

    if field == "child_gender":
        await callback.message.answer("⚧ Выбери пол ребёнка:", reply_markup=gender_keyboard())
        await callback.answer()
        return

    if field == "child_birthdate":
        await state.set_state(SetDate.waiting_birthdate)
        await callback.message.answer(
            "📅 Введи новую дату рождения ребёнка.\n"
            "Формат: <b>20.11.2024</b>"
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


@router.message(EditProfile.waiting_value)
async def profile_edit_save(message: Message, state: FSMContext, db_user: dict = None):
    data = await state.get_data()
    field = data.get("field")
    value = message.text.strip() if message.text else ""

    context = {}
    if db_user and db_user.get("child_context"):
        try:
            context = json.loads(db_user["child_context"])
        except Exception:
            pass

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
    context = {}
    if db_user and db_user.get("child_context"):
        try:
            context = json.loads(db_user["child_context"])
        except Exception:
            pass
    context["child_gender"] = gender
    await db.set_child_context(callback.from_user.id, context)
    await callback.message.edit_text(f"✅ Пол ребёнка: <b>{gender_names.get(gender, gender)}</b>")
    await callback.answer()
