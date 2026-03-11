import os
import json
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from loguru import logger

from config import Config
from keyboards.main_kb import library_keyboard, confirm_delete_keyboard
from kb.chroma_client import delete_chunks
import db.queries as db

router = Router()


def _is_admin(db_user: dict) -> bool:
    return db_user and bool(db_user.get("is_admin"))


# ─── Whitelist управление ─────────────────────────────────────────────────────

@router.message(Command("whitelist_add"))
async def cmd_whitelist_add(message: Message, db_user: dict = None):
    if not _is_admin(db_user):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: /whitelist_add <telegram_id>")
        return
    target_id = int(parts[1])
    await db.add_to_whitelist(target_id, message.from_user.id)
    await message.answer(f"✅ Пользователь {target_id} добавлен в whitelist.")


@router.message(Command("whitelist_remove"))
async def cmd_whitelist_remove(message: Message, db_user: dict = None):
    if not _is_admin(db_user):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: /whitelist_remove <telegram_id>")
        return
    target_id = int(parts[1])
    await db.remove_from_whitelist(target_id)
    await message.answer(f"✅ Пользователь {target_id} удалён из whitelist.")


# ─── Статистика KB ────────────────────────────────────────────────────────────

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


# ─── Рассылка ─────────────────────────────────────────────────────────────────

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot, db_user: dict = None):
    if not _is_admin(db_user):
        return
    text = message.text[len("/broadcast"):].strip()
    if not text:
        await message.answer("Использование: /broadcast <текст сообщения>")
        return

    users = await db.get_all_active_users()
    sent = 0
    failed = 0
    for user in users:
        try:
            await bot.send_message(user["id"], text)
            sent += 1
        except Exception as e:
            logger.warning("Broadcast failed for user {}: {}", user["id"], e)
            failed += 1

    await message.answer(f"✅ Рассылка завершена: {sent} отправлено, {failed} ошибок.")


# ─── Библиотека ───────────────────────────────────────────────────────────────

@router.message(F.text == "📖 Моя библиотека")
async def my_library(message: Message, db_user: dict = None):
    if not db_user:
        return
    user_id = message.from_user.id
    shared_books = await db.get_shared_books()
    personal_books = await db.get_personal_books(user_id)
    excluded_ids = await db.get_excluded_book_ids(user_id)

    if not shared_books and not personal_books:
        await message.answer(
            "📚 База знаний пуста.\n\nОтправь PDF-файл чтобы добавить книгу."
        )
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
    book_id = int(parts[1])
    action = parts[2]  # "exclude" или "include"

    user_id = callback.from_user.id
    if action == "exclude":
        await db.exclude_book(user_id, book_id)
    else:
        await db.include_book(user_id, book_id)

    # Обновить клавиатуру
    shared_books = await db.get_shared_books()
    personal_books = await db.get_personal_books(user_id)
    excluded_ids = await db.get_excluded_book_ids(user_id)

    await callback.message.edit_reply_markup(
        reply_markup=library_keyboard(shared_books, personal_books, excluded_ids)
    )
    label = "отключена" if action == "exclude" else "включена"
    await callback.answer(f"Книга {label}.")


@router.callback_query(F.data.startswith("book_delete:"))
async def book_delete_confirm(callback: CallbackQuery, db_user: dict = None):
    book_id = int(callback.data.split(":")[1])
    book = await db.get_book(book_id)
    if not book:
        await callback.answer("Книга не найдена.", show_alert=True)
        return

    # Только владелец или admin может удалить
    if book.get("owner_id") != callback.from_user.id and not _is_admin(db_user):
        await callback.answer("Нет прав для удаления.", show_alert=True)
        return

    await callback.message.answer(
        f"🗑 Удалить книгу <b>{book['original_name']}</b>?\n"
        "Это действие нельзя отменить.",
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

    # 1. Удалить чанки из ChromaDB
    chroma_ids = []
    if book.get("chroma_ids"):
        try:
            chroma_ids = json.loads(book["chroma_ids"])
        except Exception:
            pass

    if chroma_ids:
        try:
            delete_chunks(
                scope=book["scope"],
                user_id=book.get("owner_id"),
                chunk_ids=chroma_ids,
            )
        except Exception as e:
            logger.error("Ошибка удаления из ChromaDB: {}", e)
            await callback.message.edit_text("❌ Ошибка удаления из базы знаний. Попробуй позже.")
            await callback.answer()
            return

    # 2. Удалить из БД
    await db.delete_book(book_id)

    # 3. Удалить файл с диска
    if config:
        if book["scope"] == "shared":
            file_path = os.path.join(config.data_dir, "shared_kb", book["filename"])
        else:
            file_path = os.path.join(config.data_dir, "user_kb", str(book.get("owner_id")), book["filename"])
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.warning("Не удалось удалить файл {}: {}", file_path, e)

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
