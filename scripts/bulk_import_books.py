"""Bulk-import уже скачанных книг в shared library обходя Telegram.

Делает то же что pdf_upload.process_age_range, но без бота:
1. Копирует файл в data_dir/shared_kb/
2. extract_and_chunk → текст
3. embed_texts → векторы
4. db.add_book → запись в SQLite
5. add_chunks → запись в Chroma с правильным age_min/age_max

Возрастные диапазоны фиксируем явно (захардкожены ниже), потому что они нам
уже известны из веб-поисков по описаниям издательств.
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger
from config import load_config
from kb.chroma_client import init_chroma, add_chunks
from kb.embedder import embed_texts
from kb.pdf_processor import extract_and_chunk
import db.queries as db
from db.schema import init_db


# (filename относительно /tmp/, original_name для UI, age_min, age_max)
TO_IMPORT = [
    ("thirty_million_words_suskind.epub",
     "Тридцать миллионов слов — Дана Саскинд.epub",
     0, 36),
    ("no_drama_discipline_siegel_bryson.fb2",
     "Дисциплина без драм — Сигел, Брайсон.fb2",
     12, 168),
    ("gardener_carpenter_gopnik.fb2",
     "Садовник и плотник — Элисон Гопник.fb2",
     0, 144),
    ("child_of_mine_satter.epub",
     "Child of Mine — Ellyn Satter.epub",
     0, 72),
]


async def import_one(config, source_path: Path, original_name: str,
                     age_min: int, age_max: int):
    save_dir = Path(config.data_dir) / "shared_kb"
    save_dir.mkdir(parents=True, exist_ok=True)

    safe_name = original_name.replace("/", "_").replace("\\", "_")
    dest = save_dir / safe_name
    shutil.copyfile(source_path, dest)
    logger.info("→ {} ({:.1f} MB)", original_name, dest.stat().st_size / 1024 / 1024)

    chunks = extract_and_chunk(str(dest))
    logger.info("  chunks: {}", len(chunks))
    if len(chunks) < 5:
        logger.error("  слишком мало чанков — пропускаю")
        return

    embeddings = await asyncio.to_thread(embed_texts, chunks)

    book_id = await db.add_book(
        filename=safe_name,
        original_name=original_name,
        owner_id=None,
        scope="shared",
        age_range_min=age_min,
        age_range_max=age_max,
        chunk_count=len(chunks),
    )
    logger.info("  book_id={}", book_id)

    chunk_ids = await asyncio.to_thread(
        add_chunks,
        scope="shared",
        user_id=None,
        chunks=chunks,
        embeddings=embeddings,
        book_id=book_id,
        age_min=age_min,
        age_max=age_max,
    )
    await db.update_book_chroma_ids(book_id, chunk_ids)
    logger.info("  → {} чанков в Chroma, age={}..{}", len(chunk_ids), age_min, age_max)


async def main():
    config = load_config()
    await init_db(config.db_path, config.admin_telegram_id, config.whitelist_ids)
    init_chroma(config.chroma_dir)

    for fname, original_name, age_min, age_max in TO_IMPORT:
        source = Path("/tmp") / fname
        if not source.exists():
            logger.error("файл не найден: {}", source)
            continue
        try:
            await import_one(config, source, original_name, age_min, age_max)
        except Exception as e:
            logger.exception("FAIL: {} — {}", fname, e)


if __name__ == "__main__":
    asyncio.run(main())
