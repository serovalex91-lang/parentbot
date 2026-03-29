from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _child_button_text(child_gender: str = "", role: str = "") -> str:
    is_grandparent = role in ("grandpa", "grandma")
    if child_gender == "boy":
        return "👶 Расскажи о внуке" if is_grandparent else "👶 Расскажи о сыночке"
    elif child_gender == "girl":
        return "👶 Расскажи о внучке" if is_grandparent else "👶 Расскажи о дочке"
    else:
        return "👶 Расскажи о внуке" if is_grandparent else "👶 Расскажи о ребёнке"


def main_menu(search_mode: str = "kb_only", child_gender: str = "", role: str = "") -> ReplyKeyboardMarkup:
    mode_label = "📚 Только из книг ✓" if search_mode == "kb_only" else "🌐 Книги + интернет ✓"
    child_btn = _child_button_text(child_gender, role)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💬 Задать вопрос"), KeyboardButton(text=child_btn)],
            [KeyboardButton(text=mode_label), KeyboardButton(text="📖 Моя библиотека")],
            [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="💰 Мои расходы")],
            [KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
    )


def role_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👨 Я папа", callback_data="role:papa")
    builder.button(text="👩 Я мама", callback_data="role:mama")
    builder.button(text="👫 Советы для обоих", callback_data="role:both")
    builder.button(text="👴 Я дедушка", callback_data="role:grandpa")
    builder.button(text="👵 Я бабушка", callback_data="role:grandma")
    builder.button(text="👪 Я родственник", callback_data="role:relative")
    builder.adjust(2, 1, 2, 1)
    return builder.as_markup()


def age_range_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    ranges = [
        ("0–12 мес", "0:12"),
        ("1–3 года", "12:36"),
        ("3–7 лет", "36:84"),
        ("7–12 лет", "84:144"),
        ("12–18 лет", "144:216"),
        ("Любой возраст", "0:999"),
    ]
    for label, value in ranges:
        builder.button(text=label, callback_data=f"agerange:{value}")
    builder.button(text="🤖 Определить автоматически", callback_data="agerange:auto")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def library_keyboard(
    shared_books: list,
    personal_books: list,
    excluded_ids: list,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if shared_books:
        builder.button(text="── 📚 Общие книги ──", callback_data="noop")
        for book in shared_books:
            is_excluded = book["id"] in excluded_ids
            status = "⛔" if is_excluded else "✅"
            action = "include" if is_excluded else "exclude"
            builder.button(
                text=f"{status} {book['original_name'][:30]}",
                callback_data=f"book_toggle:{book['id']}:{action}",
            )
        builder.adjust(1)

    if personal_books:
        builder.button(text="── 📘 Мои книги ──", callback_data="noop")
        for book in personal_books:
            builder.button(
                text=f"📄 {book['original_name'][:30]}",
                callback_data="noop",
            )
            builder.button(
                text="🗑 удалить",
                callback_data=f"book_delete:{book['id']}",
            )

    builder.button(text="➕ Загрузить книгу", callback_data="book_upload")
    builder.adjust(1)
    return builder.as_markup()


def profile_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    fields = [
        ("👤 Роль", "role"),
        ("⚧ Пол ребёнка", "child_gender"),
        ("👶 Имя ребёнка", "child_name"),
        ("⚠️ Особенности", "child_features"),
        ("🌟 Характер", "child_character"),
        ("📝 Заметки", "child_notes"),
        ("📅 Дата рождения", "child_birthdate"),
        ("🎨 Стиль для меня", "my_style"),
        ("💬 Стиль для партнёра", "partner_style"),
    ]
    for label, field in fields:
        builder.button(text=f"{label} [изменить]", callback_data=f"profile_edit:{field}")
    builder.button(text="📋 Сводка о ребёнке", callback_data="child_summary")
    builder.button(text="📝 Заполнить профиль", callback_data="start_fillprofile")
    builder.adjust(1)
    return builder.as_markup()


def gender_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👦 Мальчик", callback_data="gender:boy")
    builder.button(text="👧 Девочка", callback_data="gender:girl")
    builder.adjust(2)
    return builder.as_markup()


def style_keyboard(target: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора стиля. target = 'my' или 'partner'."""
    builder = InlineKeyboardBuilder()
    styles = [
        ("🤗 Мягкий, с сопереживанием", f"style:{target}:gentle"),
        ("⚖️ Сбалансированный", f"style:{target}:balanced"),
        ("📏 Чёткий, с границами", f"style:{target}:structured"),
        ("✏️ Свой вариант", f"style:{target}:custom"),
    ]
    for label, data in styles:
        builder.button(text=label, callback_data=data)
    builder.adjust(1)
    return builder.as_markup()


def admin_keyboard(whitelist: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить пользователя", callback_data="admin:add")
    builder.button(text="➖ Удалить пользователя", callback_data="admin:remove")
    builder.button(text="📋 Список whitelist", callback_data="admin:list")
    builder.button(text="📊 Статистика KB", callback_data="admin:stats")
    builder.button(text="💰 Расходы юзеров", callback_data="admin:usage")
    builder.button(text="📢 Рассылка", callback_data="admin:broadcast")
    builder.adjust(1)
    return builder.as_markup()


def confirm_delete_keyboard(book_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"book_delete_confirm:{book_id}")
    builder.button(text="❌ Отмена", callback_data="book_delete_cancel")
    builder.adjust(2)
    return builder.as_markup()


def onboarding_skip_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить", callback_data="onboarding:skip")
    return builder.as_markup()


def onboarding_options_keyboard(options: list) -> InlineKeyboardMarkup:
    """Клавиатура с вариантами ответов для онбординга.
    options — список (value, label, hint).
    """
    builder = InlineKeyboardBuilder()
    for i, (value, label, hint) in enumerate(options):
        builder.button(text=f"{label}", callback_data=f"onb_opt:{i}")
    builder.button(text="✏️ Свой вариант", callback_data="onb_opt:custom")
    builder.button(text="Пропустить", callback_data="onboarding:skip")
    builder.adjust(1)  # по одной кнопке в ряд
    return builder.as_markup()


def review_keyboard(field: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Актуально", callback_data=f"review:keep:{field}")
    builder.button(text="Изменить", callback_data=f"review:edit:{field}")
    builder.button(text="Убрать", callback_data=f"review:delete:{field}")
    builder.adjust(3)
    return builder.as_markup()


def access_request_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Добавить", callback_data=f"access:approve:{user_id}")
    builder.button(text="❌ Отклонить", callback_data=f"access:reject:{user_id}")
    builder.adjust(2)
    return builder.as_markup()
