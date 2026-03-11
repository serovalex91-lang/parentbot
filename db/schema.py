import aiosqlite
from loguru import logger
from typing import List

SCHEMA = """
CREATE TABLE IF NOT EXISTS whitelist (
    telegram_id INTEGER PRIMARY KEY,
    added_by    INTEGER,
    added_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,
    username        TEXT,
    full_name       TEXT,
    role            TEXT CHECK(role IN ('papa', 'mama', 'both')),
    child_birthdate TEXT,
    child_context   TEXT,
    is_admin        INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    search_mode     TEXT DEFAULT 'kb_only' CHECK(search_mode IN ('kb_only', 'kb_internet')),
    created_at      TEXT DEFAULT (datetime('now')),
    onboarded_at    TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    role        TEXT CHECK(role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS books (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    original_name   TEXT NOT NULL,
    owner_id        INTEGER,
    scope           TEXT CHECK(scope IN ('shared', 'personal')),
    age_range_min   INTEGER,
    age_range_max   INTEGER,
    chunk_count     INTEGER DEFAULT 0,
    chroma_ids      TEXT,
    uploaded_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_book_exclusions (
    user_id     INTEGER NOT NULL REFERENCES users(id),
    book_id     INTEGER NOT NULL REFERENCES books(id),
    excluded_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, book_id)
);

CREATE TABLE IF NOT EXISTS age_notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    book_id     INTEGER NOT NULL,
    sent_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, book_id)
);
"""


async def init_db(db_path: str, admin_id: int, whitelist_ids: List[int]):
    async with aiosqlite.connect(db_path) as db:
        # WAL mode для лучшей конкурентности
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript(SCHEMA)

        for tg_id in whitelist_ids:
            await db.execute(
                "INSERT OR IGNORE INTO whitelist (telegram_id, added_by) VALUES (?, ?)",
                (tg_id, admin_id),
            )

        if admin_id:
            await db.execute(
                "UPDATE users SET is_admin = 1 WHERE id = ?",
                (admin_id,),
            )

        await db.commit()
    logger.info("База данных инициализирована: {}", db_path)
