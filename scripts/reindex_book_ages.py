"""Переопределить возрастной диапазон для всех книг с дефолтом 0..999.

Последствие сломанного _detect_age_range — все книги, залитые до фикса, имеют
"любой возраст" в metadata. Бот не может рекомендовать их по возрасту ребёнка.

Для каждой подходящей книги:
1. Берём первые 5 чанков из соответствующей Chroma-коллекции.
2. Спрашиваем у Gemini Flash возрастной диапазон.
3. Обновляем books.age_range_min/max в SQLite.
4. Обновляем metadata всех чанков этой книги в Chroma.

Перед изменением сохраняем бэкап старых значений в data/.reindex_backup.json.
"""
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger

from config import load_config
from kb.chroma_client import get_client as get_chroma


AGE_PROMPT = (
    "Прочитай фрагменты книги и определи для какого возраста детей она написана.\n"
    "Ответь ТОЛЬКО одной строкой в формате: MIN:MAX\n"
    "Где MIN и MAX — возраст в месяцах. Варианты:\n"
    "0:12 (младенцы 0-12 мес)\n"
    "12:36 (1-3 года)\n"
    "36:84 (3-7 лет)\n"
    "84:144 (7-12 лет)\n"
    "144:216 (12-18 лет)\n"
    "0:999 (любой возраст, универсальная)\n\n"
    "Фрагменты книги:\n{sample}"
)


def _collection_name(scope: str, owner_id):
    if scope == "shared":
        return "shared_kb"
    return f"user_{owner_id}"


async def detect_age(sample: str) -> tuple[int, int]:
    from services.claude_client import get_client
    client = get_client()
    response = await client.chat.completions.create(
        model="google/gemini-2.5-flash",
        max_tokens=20,
        messages=[{"role": "user", "content": AGE_PROMPT.format(sample=sample)}],
    )
    text = response.choices[0].message.content.strip()
    match = re.search(r"(\d+):(\d+)", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 0, 999


async def main(dry_run: bool = False, force_all: bool = False):
    config = load_config()
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row

    where = "" if force_all else "WHERE age_range_min = 0 AND age_range_max = 999"
    books = conn.execute(
        f"SELECT id, original_name, owner_id, scope, age_range_min, age_range_max "
        f"FROM books {where} ORDER BY id"
    ).fetchall()

    logger.info("К переиндексации: {} книг (dry_run={})", len(books), dry_run)

    backup_path = Path(config.data_dir) / ".reindex_backup.json"
    backup = []

    for book in books:
        book_id = book["id"]
        name = book["original_name"]
        scope = book["scope"]
        owner_id = book["owner_id"]

        collection = get_chroma().get_or_create_collection(
            name=_collection_name(scope, owner_id),
            metadata={"hnsw:space": "cosine"},
        )
        chunks = collection.get(
            where={"book_id": book_id},
            limit=5,
            include=["documents"],
        )
        docs = chunks.get("documents") or []
        if len(docs) < 1:
            logger.warning("book {} ({}): нет чанков, пропускаю", book_id, name[:40])
            continue

        sample = "\n\n".join(docs)[:3000]
        new_min, new_max = await detect_age(sample)
        logger.info(
            "book {} ({}...): {}..{} -> {}..{}",
            book_id, name[:40],
            book["age_range_min"], book["age_range_max"],
            new_min, new_max,
        )

        if dry_run:
            continue

        all_ids = collection.get(where={"book_id": book_id}, include=[])["ids"]
        backup.append({
            "book_id": book_id,
            "old_min": book["age_range_min"],
            "old_max": book["age_range_max"],
            "chunk_ids": all_ids,
        })

        conn.execute(
            "UPDATE books SET age_range_min=?, age_range_max=? WHERE id=?",
            (new_min, new_max, book_id),
        )
        conn.commit()

        if all_ids:
            existing = collection.get(ids=all_ids, include=["metadatas"])
            new_metas = []
            for meta in existing["metadatas"]:
                meta = dict(meta)
                meta["age_min"] = new_min
                meta["age_max"] = new_max
                new_metas.append(meta)
            collection.update(ids=all_ids, metadatas=new_metas)

        logger.info("  -> обновлено {} чанков", len(all_ids))

    if backup and not dry_run:
        backup_path.write_text(json.dumps(backup, indent=2, ensure_ascii=False))
        logger.info("Бэкап старых значений: {}", backup_path)

    conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    force = "--force-all" in sys.argv
    asyncio.run(main(dry_run=dry, force_all=force))
