import os
import json
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from loguru import logger

from config import Config
from states.fsm import AdminPanel
from keyboards.main_kb import library_keyboard, confirm_delete_keyboard, admin_keyboard
from kb.chroma_client import delete_chunks
import db.queries as db

router = Router()


def _is_admin(db_user: dict) -> bool:
    return db_user and bool(db_user.get("is_admin"))


@router.message(Command("admin"))
async def cmd_admin(message: Message, db_user: dict = None):
    if not _is_admin(db_user):
        return
    whitelist = await db.get_whitelist()
    stats = await db.get_kb_stats()
    await message.answer(
        "<b>🔧 Панель администратора</b>\n\n"
        f"👥 В whitelist: <b>{len(whitelist)}</b> чел.\n"
        f"📚 Общих книг: <b>{stats['shared_books']}</b> ({stats['shared_chunks']} фрагментов)\n"
        f"👤 Активных пользователей: <b>{stats['users']}</b>",
        reply_markup=admin_keyboard(whitelist),
    )


@router.callback_query(F.data == "admin:list")
async def admin_list(callback: CallbackQuery, db_user: dict = None):
    if not _is_admin(db_user):
        await callback.answer("Нет прав.", show_alert=True)
        return
    whitelist = await db.get_whitelist()
    if not whitelist:
        await callback.answer("Whitelist пуст.", show_alert=True)
        return
    lines = [f"• <code>{row['telegram_id']}</code> (добавлен {row['added_at'][:10]})" for row in whitelist]
    await callback.message.answer("📋 <b>Whitelist:</b>\n\n" + "\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "admin:add")
async def admin_add_start(callback: CallbackQuery, state: FSMContext, db_user: dict = None):
    if not _is_admin(db_user):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.set_state(AdminPanel.waiting_add_id)
    await callback.message.answer(
        "➕ Введи Telegram ID пользователя:\n<i>Узнать ID — @userinfobot</i>"
    )
    await callback.answer()


@router.message(AdminPanel.waiting_add_id)
async def admin_add_execute(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ ID должен быть числом. Попробуй ещё раз:")
        return
    target_id = int(text)
    await db.add_to_whitelist(target_id, message.from_user.id)
    await state.clear()
    await message.answer(f"✅ Пользователь <code>{target_id}</code> добавлен в whitelist.")


@router.callback_query(F.data == "admin:remove")
async def admin_remove_start(callback: CallbackQuery, state: FSMContext, db_user: dict = None):
    if not _is_admin(db_user):
        await callback.answer("Нет прав.", show_alert=True)
        return
    whitelist = await db.get_whitelist()
    lines = [f"• <code>{row['telegram_id']}</code>" for row in whitelist]
    await state.set_state(AdminPanel.waiting_remove_id)
    await callback.message.answer("➖ Введи Telegram ID для удаления:\n\n" + "\n".join(lines))
    await callback.answer()


@router.message(AdminPanel.waiting_remove_id)
async def admin_remove_execute(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ ID должен быть числом. Попробуй ещё раз:")
        return
    target_id = int(text)
    await db.remove_from_whitelist(target_id)
    await state.clear()
    await message.answer(f"✅ Пользователь <code>{target_id}</code> удалён из whitelist.")


@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery, db_user: dict = None):
    if not _is_admin(db_user):
        await callback.answer("Нет прав.", show_alert=True)
        return
    stats = await db.get_kb_stats()
    await callback.message.answer(
        "<b>📊 Статистика базы знаний</b>\n\n"
        f"📚 Общих книг: <b>{stats['shared_books']}</b> ({stats['shared_chunks']} фрагментов)\n"
        f"📘 Личных книг всего: <b>{stats['personal_books']}</b>\n"
        f"👤 Активных пользователей: <b>{stats['users']}</b>"
    )
    await callback.answer()


@router.callback_query(F.data == "admin:usage")
async def admin_usage(callback: CallbackQuery, db_user: dict = None):
    if not _is_admin(db_user):
        await callback.answer("Нет прав.", show_alert=True)
        return
    users_stats = await db.get_all_users_usage_stats()
    if not users_stats:
        await callback.message.answer("Нет данных о расходах.")
        await callback.answer()
        return

    total_cost = sum(u["total_cost"] for u in users_stats)
    total_tokens = sum(u["total_input"] + u["total_output"] for u in users_stats)
    total_requests = sum(u["total_requests"] for u in users_stats)

    lines = [f"<b>💰 Расходы по юзерам</b>\n"]
    lines.append(
        f"Всего: <b>{total_requests}</b> запросов, "
        f"<b>{total_tokens:,}</b> токенов, "
        f"<b>${total_cost:.4f}</b>\n"
    )
    for u in users_stats:
        if u["total_requests"] == 0:
            continue
        name = u["full_name"] or u["username"] or str(u["id"])
        tok = u["total_input"] + u["total_output"]
        lines.append(
            f"<b>{name}</b>: {u['total_requests']} запр., "
            f"{tok:,} tok, ${u['total_cost']:.4f}"
        )
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext, db_user: dict = None):
    if not _is_admin(db_user):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.set_state(AdminPanel.waiting_broadcast_text)
    await callback.message.answer("📢 Введи текст рассылки:")
    await callback.answer()


@router.message(AdminPanel.waiting_broadcast_text)
async def admin_broadcast_execute(message: Message, state: FSMContext, bot: Bot):
    text = message.text or ""
    if not text.strip():
        await message.answer("❌ Текст не может быть пустым.")
        return
    users = await db.get_all_active_users()
    sent, failed = 0, 0
    for user in users:
        try:
            await bot.send_message(user["id"], text)
            sent += 1
        except Exception as e:
            logger.warning("Broadcast failed for {}: {}", user["id"], e)
            failed += 1
    await state.clear()
    await message.answer(f"✅ Рассылка: {sent} отправлено, {failed} ошибок.")


@router.message(Command("whitelist_add"))
async def cmd_whitelist_add(message: Message, db_user: dict = None):
    if not _is_admin(db_user):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: /whitelist_add <telegram_id>")
        return
    await db.add_to_whitelist(int(parts[1]), message.from_user.id)
    await message.answer(f"✅ Пользователь {parts[1]} добавлен.")


@router.message(Command("whitelist_remove"))
async def cmd_whitelist_remove(message: Message, db_user: dict = None):
    if not _is_admin(db_user):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: /whitelist_remove <telegram_id>")
        return
    await db.remove_from_whitelist(int(parts[1]))
    await message.answer(f"✅ Пользователь {parts[1]} удалён.")


@router.message(Command("kb_stats"))
async def cmd_kb_stats(message: Message, db_user: dict = None):
    if not _is_admin(db_user):
        return
    stats = await db.get_kb_stats()
    await message.answer(
        "<b>📊 Статистика базы знаний</b>\n\n"
        f"📚 Общих книг: <b>{stats['shared_books']}</b> ({stats['shared_chunks']} фрагментов)\n"
        f"📘 Личных книг всего: <b>{stats['personal_books']}</b>\n"
        f"👤 Активных пользователей: <b>{stats['users']}</b>"
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot, db_user: dict = None):
    if not _is_admin(db_user):
        return
    text = message.text[len("/broadcast"):].strip()
    if not text:
        await message.answer("Использование: /broadcast <текст>")
        return
    users = await db.get_all_active_users()
    sent, failed = 0, 0
    for user in users:
        try:
            await bot.send_message(user["id"], text)
            sent += 1
        except Exception as e:
            logger.warning("Broadcast failed for {}: {}", user["id"], e)
            failed += 1
    await message.answer(f"✅ Рассылка: {sent} отправлено, {failed} ошибок.")


@router.message(Command("broadcast_parents"))
async def cmd_broadcast_parents(message: Message, bot: Bot, db_user: dict = None):
    """Рассылка только для papa/mama/both."""
    if not _is_admin(db_user):
        return
    text = message.text[len("/broadcast_parents"):].strip()
    if not text:
        await message.answer("Использование: /broadcast_parents <текст>")
        return
    users = await db.get_users_by_roles(["papa", "mama", "both"])
    sent, failed = 0, 0
    for user in users:
        try:
            await bot.send_message(user["id"], text)
            sent += 1
        except Exception as e:
            logger.warning("Broadcast (parents) failed for {}: {}", user["id"], e)
            failed += 1
    await message.answer(f"✅ Рассылка родителям: {sent} отправлено, {failed} ошибок.")


@router.message(F.text == "📖 Моя библиотека")
async def my_library(message: Message, db_user: dict = None):
    if not db_user:
        return
    user_id = message.from_user.id
    shared_books = await db.get_shared_books()
    personal_books = await db.get_personal_books(user_id)
    excluded_ids = await db.get_excluded_book_ids(user_id)
    if not shared_books and not personal_books:
        await message.answer("📚 База знаний пуста.\n\nОтправь PDF-файл чтобы добавить книгу.")
        return
    await message.answer(
        "📖 <b>Моя библиотека</b>\n\n"
        "✅ — книга активна, ⛔ — отключена для тебя.\n"
        "Нажми на книгу чтобы включить/отключить:",
        reply_markup=library_keyboard(shared_books, personal_books, excluded_ids),
    )


@router.callback_query(F.data.startswith("book_toggle:"))
async def book_toggle(callback: CallbackQuery, db_user: dict = None):
    parts = callback.data.split(":")
    book_id, action = int(parts[1]), parts[2]
    user_id = callback.from_user.id
    if action == "exclude":
        await db.exclude_book(user_id, book_id)
    else:
        await db.include_book(user_id, book_id)
    shared_books = await db.get_shared_books()
    personal_books = await db.get_personal_books(user_id)
    excluded_ids = await db.get_excluded_book_ids(user_id)
    await callback.message.edit_reply_markup(
        reply_markup=library_keyboard(shared_books, personal_books, excluded_ids)
    )
    await callback.answer("включена" if action == "include" else "отключена")


@router.callback_query(F.data.startswith("book_delete:"))
async def book_delete_confirm(callback: CallbackQuery, db_user: dict = None):
    book_id = int(callback.data.split(":")[1])
    book = await db.get_book(book_id)
    if not book:
        await callback.answer("Книга не найдена.", show_alert=True)
        return
    if book.get("owner_id") != callback.from_user.id and not _is_admin(db_user):
        await callback.answer("Нет прав.", show_alert=True)
        return
    await callback.message.answer(
        f"🗑 Удалить книгу <b>{book['original_name']}</b>?\nЭто нельзя отменить.",
        reply_markup=confirm_delete_keyboard(book_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("book_delete_confirm:"))
async def book_delete_execute(callback: CallbackQuery, config: Config = None, db_user: dict = None):
    book_id = int(callback.data.split(":")[1])
    book = await db.get_book(book_id)
    if not book:
        await callback.answer("Книга не найдена.", show_alert=True)
        return
    if book.get("owner_id") != callback.from_user.id and not _is_admin(db_user):
        await callback.answer("Нет прав.", show_alert=True)
        return
    chroma_ids = []
    if book.get("chroma_ids"):
        try:
            chroma_ids = json.loads(book["chroma_ids"])
        except Exception:
            pass
    if chroma_ids:
        try:
            delete_chunks(scope=book["scope"], user_id=book.get("owner_id"), chunk_ids=chroma_ids)
        except Exception as e:
            logger.error("Ошибка удаления из ChromaDB: {}", e)
            await callback.message.edit_text("❌ Ошибка удаления. Попробуй позже.")
            await callback.answer()
            return
    await db.delete_book(book_id)
    if config:
        folder = "shared_kb" if book["scope"] == "shared" else f"user_kb/{book.get('owner_id')}"
        path = os.path.join(config.data_dir, folder, book["filename"])
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logger.warning("Не удалось удалить файл: {}", e)
    await callback.message.edit_text(f"✅ Книга <b>{book['original_name']}</b> удалена.")
    await callback.answer()


@router.callback_query(F.data == "book_delete_cancel")
async def book_delete_cancel(callback: CallbackQuery):
    await callback.message.edit_text("❌ Удаление отменено.")
    await callback.answer()


@router.callback_query(F.data == "book_upload")
async def book_upload_prompt(callback: CallbackQuery):
    await callback.message.answer("📎 Отправь PDF-файл (до 20 MB) — я добавлю его в базу знаний.")
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()
