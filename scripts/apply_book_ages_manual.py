"""Применить вручную выверенные возрастные диапазоны книг (на основе
веб-поиска и описаний издателей, а не угадывания из текста).

Маппинг book_id → (age_min_months, age_max_months) основан на:
- описаниях с Литрес/Лабиринт/Эксмо
- структуре книги (целевая аудитория)
- отзывах и рецензиях

Перед применением сохраняет бэкап старых значений в data/.reindex_backup.json.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger
from config import load_config
from kb.chroma_client import init_chroma, get_client as get_chroma


# id → (min_months, max_months, описание для лога)
BOOK_AGES = {
    2:  (0,   144, "Петрановская - Тайная опора (0-12)"),
    3:  (24,  168, "Ле Шан - Когда ваш ребёнок сводит вас с ума (2-14)"),
    4:  (24,  168, "Фабер/Мазлиш - Как говорить чтобы дети слушали (2-14)"),
    5:  (0,   36,  "Циммер - Компетентный малыш (0-3)"),
    6:  (0,   60,  "Lancet ECD Executive Summary (0-5)"),
    7:  (0,   144, "Катасонов - Федиатрия (0-12)"),
    8:  (0,   144, "Комаровский - Здоровье ребёнка (0-12)"),
    9:  (24,  144, "Бекки Кеннеди - Помочь ребёнку быть хорошим (2-12)"),
    10: (0,   216, "Ньюфелд - Не упускайте своих детей (0-18)"),
    11: (0,   24,  "Попова - У вас дома младенец (0-2)"),
    12: (0,   144, "Шиян - Рота вирусов (0-12)"),
    13: (132, 216, "Сигел - Растущий мозг / Brainstorm (11-18)"),
    14: (0,   144, "Сигел/Брайсон - Whole Brain Child (0-12)"),
}


def _collection_name(scope, owner_id):
    return "shared_kb" if scope == "shared" else f"user_{owner_id}"


def main(dry_run: bool = False):
    config = load_config()
    init_chroma(config.chroma_dir)
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row

    backup = []
    chroma = get_chroma()

    for book_id, (new_min, new_max, note) in BOOK_AGES.items():
        row = conn.execute(
            "SELECT id, original_name, scope, owner_id, age_range_min, age_range_max "
            "FROM books WHERE id=?",
            (book_id,),
        ).fetchone()
        if not row:
            logger.warning("book {} не найдена в БД, пропускаю ({})", book_id, note)
            continue

        old_min = row["age_range_min"]
        old_max = row["age_range_max"]
        scope = row["scope"]
        owner_id = row["owner_id"]

        logger.info(
            "book {} ({}): {}..{} -> {}..{}",
            book_id, note, old_min, old_max, new_min, new_max,
        )

        if dry_run:
            continue
        if old_min == new_min and old_max == new_max:
            logger.info("  уже правильно, пропускаю")
            continue

        collection = chroma.get_or_create_collection(
            name=_collection_name(scope, owner_id),
            metadata={"hnsw:space": "cosine"},
        )
        all_ids = collection.get(where={"book_id": book_id}, include=[])["ids"]

        backup.append({
            "book_id": book_id,
            "old_min": old_min,
            "old_max": old_max,
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
                m = dict(meta)
                m["age_min"] = new_min
                m["age_max"] = new_max
                new_metas.append(m)
            collection.update(ids=all_ids, metadatas=new_metas)
            logger.info("  -> обновлено {} чанков в Chroma", len(all_ids))

    if backup and not dry_run:
        bp = Path(config.data_dir) / ".reindex_backup_manual.json"
        bp.write_text(json.dumps(backup, indent=2, ensure_ascii=False))
        logger.info("Бэкап старых значений: {}", bp)

    conn.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
