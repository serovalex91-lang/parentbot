import json
from typing import Optional, List, Dict, Any
import aiosqlite

# Глобальный путь к БД и пул соединений
_db_path: str = ""
_connection: Optional[aiosqlite.Connection] = None


def set_db_path(path: str):
    global _db_path
    _db_path = path


def get_db_path() -> str:
    return _db_path


async def get_db() -> aiosqlite.Connection:
    """Возвращает общее соединение (с WAL mode)."""
    global _connection
    if _connection is None:
        _connection = await aiosqlite.connect(_db_path)
        await _connection.execute("PRAGMA journal_mode=WAL")
        await _connection.execute("PRAGMA busy_timeout=5000")
        _connection.row_factory = aiosqlite.Row
    return _connection


async def close_db():
    """Закрыть общее соединение при shutdown."""
    global _connection
    if _connection:
        await _connection.close()
        _connection = None


# ─── Whitelist ────────────────────────────────────────────────────────────────

async def get_whitelist() -> List[Dict[str, Any]]:
    db = await get_db()
    async with db.execute("SELECT * FROM whitelist ORDER BY added_at") as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def is_whitelisted(telegram_id: int) -> bool:
    db = await get_db()
    async with db.execute(
        "SELECT 1 FROM whitelist WHERE telegram_id = ?", (telegram_id,)
    ) as cur:
        return await cur.fetchone() is not None


async def add_to_whitelist(telegram_id: int, added_by: int):
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO whitelist (telegram_id, added_by) VALUES (?, ?)",
        (telegram_id, added_by),
    )
    await db.commit()


async def remove_from_whitelist(telegram_id: int):
    db = await get_db()
    await db.execute("DELETE FROM whitelist WHERE telegram_id = ?", (telegram_id,))
    await db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (telegram_id,))
    await db.commit()


# ─── Users ────────────────────────────────────────────────────────────────────

async def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM users WHERE id = ?", (telegram_id,)
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def upsert_user(telegram_id: int, username: str, full_name: str):
    db = await get_db()
    await db.execute(
        """INSERT INTO users (id, username, full_name)
           VALUES (?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name""",
        (telegram_id, username, full_name),
    )
    await db.commit()


async def set_user_role(telegram_id: int, role: str):
    db = await get_db()
    await db.execute("UPDATE users SET role = ? WHERE id = ?", (role, telegram_id))
    await db.commit()


async def set_user_birthdate(telegram_id: int, birthdate: str):
    db = await get_db()
    await db.execute(
        "UPDATE users SET child_birthdate = ?, onboarded_at = datetime('now') WHERE id = ?",
        (birthdate, telegram_id),
    )
    await db.execute(
        "DELETE FROM age_notifications WHERE user_id = ?", (telegram_id,)
    )
    await db.commit()


async def set_search_mode(telegram_id: int, mode: str):
    db = await get_db()
    await db.execute(
        "UPDATE users SET search_mode = ? WHERE id = ?", (mode, telegram_id)
    )
    await db.commit()


async def set_child_context(telegram_id: int, context: Dict):
    db = await get_db()
    await db.execute(
        "UPDATE users SET child_context = ? WHERE id = ?",
        (json.dumps(context, ensure_ascii=False), telegram_id),
    )
    await db.commit()


async def set_last_onboarding_prompt(telegram_id: int):
    db = await get_db()
    await db.execute(
        "UPDATE users SET last_onboarding_prompt = datetime('now') WHERE id = ?",
        (telegram_id,),
    )
    await db.commit()


async def get_all_active_users() -> List[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM users WHERE is_active = 1 AND onboarded_at IS NOT NULL"
    ) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ─── Messages (история) ───────────────────────────────────────────────────────

async def add_message(user_id: int, role: str, content: str):
    db = await get_db()
    await db.execute(
        "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content),
    )
    await db.commit()


async def get_last_messages(
    user_id: int, limit: int = 20, session_gap_hours: int = 4
) -> List[Dict[str, Any]]:
    """Возвращает последние сообщения в рамках текущей сессии.

    Сессия обрывается, если между двумя соседними сообщениями
    прошло больше session_gap_hours часов.
    """
    db = await get_db()
    # Берём чуть больше, чтобы найти разрыв сессии
    fetch_limit = limit * 2
    async with db.execute(
        """SELECT role, content, created_at FROM messages
           WHERE user_id = ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (user_id, fetch_limit),
    ) as cur:
        rows = await cur.fetchall()
        rows = [dict(r) for r in rows]  # newest first

    if not rows:
        return []

    # Ищем разрыв сессии (gap > N часов между соседними сообщениями)
    from datetime import datetime, timedelta
    session_messages = [rows[0]]
    for i in range(1, len(rows)):
        try:
            t_newer = datetime.fromisoformat(rows[i - 1]["created_at"])
            t_older = datetime.fromisoformat(rows[i]["created_at"])
            if (t_newer - t_older) > timedelta(hours=session_gap_hours):
                break  # нашли разрыв — старые сообщения не берём
        except (ValueError, TypeError):
            pass
        session_messages.append(rows[i])

    # Обрезаем до limit и разворачиваем в хронологический порядок
    session_messages = session_messages[:limit]
    result = [{"role": m["role"], "content": m["content"]} for m in reversed(session_messages)]
    return result


async def prune_old_messages(user_id: int, keep: int = 100):
    """Удаляет старые сообщения, оставляя последние keep штук."""
    db = await get_db()
    await db.execute(
        """DELETE FROM messages WHERE user_id = ? AND id NOT IN (
            SELECT id FROM messages WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        )""",
        (user_id, user_id, keep),
    )
    await db.commit()


# ─── Books ────────────────────────────────────────────────────────────────────

async def add_book(
    filename: str,
    original_name: str,
    owner_id: Optional[int],
    scope: str,
    age_range_min: int,
    age_range_max: int,
    chunk_count: int,
    chroma_ids: Optional[List[str]] = None,
) -> int:
    db = await get_db()
    cur = await db.execute(
        """INSERT INTO books
           (filename, original_name, owner_id, scope, age_range_min, age_range_max, chunk_count, chroma_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            filename,
            original_name,
            owner_id,
            scope,
            age_range_min,
            age_range_max,
            chunk_count,
            json.dumps(chroma_ids or []),
        ),
    )
    await db.commit()
    return cur.lastrowid


async def update_book_chroma_ids(book_id: int, chroma_ids: List[str]):
    db = await get_db()
    await db.execute(
        "UPDATE books SET chroma_ids = ? WHERE id = ?",
        (json.dumps(chroma_ids), book_id),
    )
    await db.commit()


async def get_book(book_id: int) -> Optional[Dict[str, Any]]:
    db = await get_db()
    async with db.execute("SELECT * FROM books WHERE id = ?", (book_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_shared_books() -> List[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM books WHERE scope = 'shared' ORDER BY uploaded_at"
    ) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_personal_books(user_id: int) -> List[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM books WHERE scope = 'personal' AND owner_id = ? ORDER BY uploaded_at",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def delete_book(book_id: int):
    db = await get_db()
    await db.execute("DELETE FROM user_book_exclusions WHERE book_id = ?", (book_id,))
    await db.execute("DELETE FROM age_notifications WHERE book_id = ?", (book_id,))
    await db.execute("DELETE FROM books WHERE id = ?", (book_id,))
    await db.commit()


async def get_kb_stats() -> Dict[str, int]:
    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*), COALESCE(SUM(chunk_count), 0) FROM books WHERE scope = 'shared'"
    ) as cur:
        shared_books, shared_chunks = await cur.fetchone()
    async with db.execute(
        "SELECT COUNT(*) FROM books WHERE scope = 'personal'"
    ) as cur:
        personal_books = (await cur.fetchone())[0]
    async with db.execute(
        "SELECT COUNT(*) FROM users WHERE is_active = 1"
    ) as cur:
        user_count = (await cur.fetchone())[0]
    return {
        "shared_books": shared_books,
        "shared_chunks": shared_chunks,
        "personal_books": personal_books,
        "users": user_count,
    }


# ─── User Book Exclusions ─────────────────────────────────────────────────────

async def exclude_book(user_id: int, book_id: int):
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO user_book_exclusions (user_id, book_id) VALUES (?, ?)",
        (user_id, book_id),
    )
    await db.commit()


async def include_book(user_id: int, book_id: int):
    db = await get_db()
    await db.execute(
        "DELETE FROM user_book_exclusions WHERE user_id = ? AND book_id = ?",
        (user_id, book_id),
    )
    await db.commit()


async def get_excluded_book_ids(user_id: int) -> List[int]:
    db = await get_db()
    async with db.execute(
        "SELECT book_id FROM user_book_exclusions WHERE user_id = ?", (user_id,)
    ) as cur:
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def is_book_excluded(user_id: int, book_id: int) -> bool:
    db = await get_db()
    async with db.execute(
        "SELECT 1 FROM user_book_exclusions WHERE user_id = ? AND book_id = ?",
        (user_id, book_id),
    ) as cur:
        return await cur.fetchone() is not None


# ─── Age Notifications ────────────────────────────────────────────────────────

async def mark_notification_sent(user_id: int, book_id: int):
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO age_notifications (user_id, book_id) VALUES (?, ?)",
        (user_id, book_id),
    )
    await db.commit()


async def was_notification_sent(user_id: int, book_id: int) -> bool:
    db = await get_db()
    async with db.execute(
        "SELECT 1 FROM age_notifications WHERE user_id = ? AND book_id = ?",
        (user_id, book_id),
    ) as cur:
        return await cur.fetchone() is not None


# ─── Token Usage ─────────────────────────────────────────────────────────────

async def add_token_usage(
    user_id: int, model: str, input_tokens: int, output_tokens: int, cost_usd: float
):
    db = await get_db()
    await db.execute(
        """INSERT INTO token_usage (user_id, model, input_tokens, output_tokens, cost_usd)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, model, input_tokens, output_tokens, cost_usd),
    )
    await db.commit()


async def get_user_usage_stats(user_id: int) -> Dict[str, Any]:
    """Суммарная статистика токенов и расходов для юзера."""
    db = await get_db()
    async with db.execute(
        """SELECT
            COUNT(*) as total_requests,
            COALESCE(SUM(input_tokens), 0) as total_input,
            COALESCE(SUM(output_tokens), 0) as total_output,
            COALESCE(SUM(cost_usd), 0.0) as total_cost
           FROM token_usage WHERE user_id = ?""",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
        return dict(row)


# ─── Access Requests ─────────────────────────────────────────────────────────

async def get_access_request(telegram_id: int) -> Optional[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM access_requests WHERE telegram_id = ?", (telegram_id,)
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def create_access_request(telegram_id: int, username: str, full_name: str):
    db = await get_db()
    await db.execute(
        """INSERT INTO access_requests (telegram_id, username, full_name, status)
           VALUES (?, ?, ?, 'pending')
           ON CONFLICT(telegram_id) DO UPDATE SET
               username = excluded.username,
               full_name = excluded.full_name,
               status = 'pending',
               requested_at = datetime('now'),
               resolved_at = NULL""",
        (telegram_id, username, full_name),
    )
    await db.commit()


async def resolve_access_request(telegram_id: int, status: str):
    db = await get_db()
    await db.execute(
        "UPDATE access_requests SET status = ?, resolved_at = datetime('now') WHERE telegram_id = ?",
        (status, telegram_id),
    )
    await db.commit()


async def get_all_users_usage_stats() -> List[Dict[str, Any]]:
    """Статистика расходов по всем юзерам (для админки)."""
    db = await get_db()
    async with db.execute(
        """SELECT
            u.id, u.username, u.full_name,
            COUNT(t.id) as total_requests,
            COALESCE(SUM(t.input_tokens), 0) as total_input,
            COALESCE(SUM(t.output_tokens), 0) as total_output,
            COALESCE(SUM(t.cost_usd), 0.0) as total_cost
           FROM users u
           LEFT JOIN token_usage t ON u.id = t.user_id
           WHERE u.is_active = 1
           GROUP BY u.id
           ORDER BY total_cost DESC""",
    ) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
