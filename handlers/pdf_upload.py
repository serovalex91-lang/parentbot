import os
import re
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, Document
from aiogram.fsm.context import FSMContext
from loguru import logger

from config import Config
from states.fsm import UploadPDF
from keyboards.main_kb import age_range_keyboard
import db.queries as db
from kb.pdf_processor import extract_and_chunk
from kb.embedder import embed_texts
from kb.chroma_client import add_chunks

router = Router()

MAX_PDF_SIZE = 20 * 1024 * 1024  # 20 MB

AGE_LABELS = {
    "0:12": "0–12 мес",
    "12:36": "1–3 года",
    "36:84": "3–7 лет",
    "84:144": "7–12 лет",
    "144:216": "12–18 лет",
    "0:999": "любой возраст",
}


async def _detect_age_range(chunks: list, config: Config) -> tuple[int, int]:
    """Спрашивает Claude какой возрастной диапазон у книги. Возвращает (age_min, age_max)."""
    from services.claude_client import get_client
    sample = "\n\n".join(chunks[:5])[:3000]
    prompt = (
        "Прочитай фрагменты книги и определи для какого возраста детей она написана.\n"
        "Ответь ТОЛЬКО одной строкой в формате: MIN:MAX\n"
        "Где MIN и MAX — возраст в месяцах. Варианты:\n"
        "0:12 (младенцы 0-12 мес)\n"
        "12:36 (1-3 года)\n"
        "36:84 (3-7 лет)\n"
        "84:144 (7-12 лет)\n"
        "144:216 (12-18 лет)\n"
        "0:999 (любой возраст, универсальная)\n\n"
        f"Фрагменты книги:\n{sample}"
    )
    try:
        client = get_client()
        response = await client.messages.create(
            model=config.claude_model,
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        match = re.search(r"(\d+):(\d+)", text)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception as e:
        logger.warning("Ошибка автоопределения возраста: {}", e)
    return 0, 999  # fallback: любой возраст


@router.message(F.document)
async def handle_document(message: Message, state: FSMContext, config: Config = None, db_user: dict = None):
    doc: Document = message.document
    if not doc.mime_type or doc.mime_type != "application/pdf":
        await message.answer("⚠️ Пожалуйста, отправь PDF-файл.")
        return

    if doc.file_size and doc.file_size > MAX_PDF_SIZE:
        await message.answer("⚠️ Файл слишком большой. Максимум 20 MB.")
        return

    await state.update_data(
        file_id=doc.file_id,
        original_name=doc.file_name or "book.pdf",
    )
    await state.set_state(UploadPDF.waiting_age_range)
    await message.answer(
        f"📄 Получил файл: <b>{doc.file_name}</b>\n\n"
        "Для какого возраста эта книга?",
        reply_markup=age_range_keyboard(),
    )


@router.callback_query(UploadPDF.waiting_age_range, F.data.startswith("agerange:"))
async def process_age_range(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    config: Config = None,
    db_user: dict = None,
):
    parts = callback.data.split(":")
    is_auto = parts[1] == "auto"

    if not is_auto:
        age_min = int(parts[1])
        age_max = int(parts[2])
    else:
        age_min = age_max = None  # определим после скачивания

    data = await state.get_data()
    file_id = data["file_id"]
    original_name = data["original_name"]

    await state.clear()
    await callback.answer()
    await callback.message.edit_text("⏳ Скачиваю и обрабатываю файл...")

    user_id = callback.from_user.id
    is_admin = db_user and db_user.get("is_admin")

    # Определить область и путь сохранения
    if is_admin:
        scope = "shared"
        owner_id = None
        save_dir = os.path.join(config.data_dir, "shared_kb")
    else:
        scope = "personal"
        owner_id = user_id
        save_dir = os.path.join(config.data_dir, "user_kb", str(user_id))

    os.makedirs(save_dir, exist_ok=True)

    # Скачать файл
    safe_name = original_name.replace("/", "_").replace("\\", "_")
    pdf_path = os.path.join(save_dir, safe_name)
    try:
        file = await bot.get_file(file_id)
        await bot.download_file(file.file_path, destination=pdf_path)
    except Exception as e:
        logger.error("Ошибка скачивания PDF: {}", e)
        await callback.message.edit_text("❌ Не удалось скачать файл. Попробуй ещё раз.")
        return

    # Обработать PDF
    try:
        chunks = extract_and_chunk(pdf_path)
    except Exception as e:
        logger.error("Ошибка парсинга PDF {}: {}", pdf_path, e)
        await callback.message.edit_text("❌ Не удалось прочитать PDF. Попробуй другой файл.")
        return

    if len(chunks) < 5:
        await callback.message.edit_text(
            "⚠️ Не удалось извлечь текст из файла.\n"
            "Возможно, это скан — попробуй другой файл."
        )
        return

    # Автоопределение возраста
    if is_auto:
        await callback.message.edit_text("🤖 Анализирую содержимое книги...")
        age_min, age_max = await _detect_age_range(chunks, config)
        age_label = AGE_LABELS.get(f"{age_min}:{age_max}", f"{age_min}–{age_max} мес")
        await callback.message.edit_text(
            f"🤖 Определил возраст: <b>{age_label}</b>\n"
            f"⏳ Создаю эмбеддинги ({len(chunks)} фрагментов)..."
        )
    else:
        await callback.message.edit_text(f"⏳ Создаю эмбеддинги ({len(chunks)} фрагментов)...")

    # Создать эмбеддинги
    try:
        embeddings = embed_texts(chunks)
    except Exception as e:
        logger.error("Ошибка эмбеддингов: {}", e)
        await callback.message.edit_text("❌ Ошибка при создании эмбеддингов.")
        return

    # Сохранить книгу в БД
    book_id = await db.add_book(
        filename=safe_name,
        original_name=original_name,
        owner_id=owner_id,
        scope=scope,
        age_range_min=age_min,
        age_range_max=age_max,
        chunk_count=len(chunks),
    )

    # Добавить в ChromaDB
    try:
        chunk_ids = add_chunks(
            scope=scope,
            user_id=owner_id,
            chunks=chunks,
            embeddings=embeddings,
            book_id=book_id,
            age_min=age_min,
            age_max=age_max,
        )
    except Exception as e:
        logger.error("Ошибка добавления в ChromaDB: {}", e)
        await callback.message.edit_text("❌ Ошибка при индексации. Попробуй ещё раз.")
        return

    # Сохранить IDs чанков в БД
    await db.update_book_chroma_ids(book_id, chunk_ids)

    age_label = AGE_LABELS.get(f"{age_min}:{age_max}", f"{age_min}–{age_max} мес")
    auto_note = " (определён автоматически 🤖)" if is_auto else ""
    shared_note = "\n📚 Книга добавлена в <b>общую базу</b> и доступна всем пользователям." if scope == "shared" else ""

    await callback.message.edit_text(
        f"✅ Книга добавлена!\n\n"
        f"📄 <b>{original_name}</b>\n"
        f"👶 Возраст: {age_label}{auto_note}\n"
        f"🔢 Проиндексировано фрагментов: <b>{len(chunks)}</b>"
        f"{shared_note}"
    )
    logger.info(
        "PDF загружен: book_id={}, scope={}, chunks={}, age={}:{}, user={}",
        book_id, scope, len(chunks), age_min, age_max, user_id,
    )
