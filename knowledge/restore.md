# ParentBot — восстановление из бэкапа

## Источник бэкапа

- Снят: 2026-06-17 05:27 UTC с BrainServer (hostkey198194, uptime 20 дней)
- Архив: `_backup_brainserver_20260617/parentbot-backup-20260617.tar.gz` (96 MB)
- sha256: `24e6d20b415b4abc05681329bfb0bf92f78eb52a003c5c8cbf8003b9cbcf00d7`
- БД integrity: ok

## Содержимое

| Что | Размер | Назначение |
|---|---|---|
| `parentbot.db` | 1.2 MB | SQLite: users(8), messages(282), books(18), token_usage(237), whitelist(9), access_requests(5), age_notifications(1) |
| `chroma/` | 88 MB | ChromaDB векторное хранилище (эмбеддинги книг) |
| `shared_kb/` | 61 MB | Общая база знаний (PDF/обработанные книги) |
| `user_kb/` | 8 KB | Пользовательские заметки |
| `announcements/` | 4 KB | История рассылок |
| `db_bak/` | 3 MB | Старые SQLite бэкапы (21 мая) |
| `logs/parentbot.log` | 76 KB | Последний лог |
| `.env` | 532 B | BOT_TOKEN, ANTHROPIC_API_KEY, BRAVE_API_KEY |
| `parentbot.service` | 312 B | systemd unit |
| `.reindex_backup_manual.json` | 118 KB | Снапшот ручной переиндексации |
| `inventory.txt` | — | Сводка состояния на момент снимка |

## Восстановление на новом сервере

1. `git clone https://github.com/serovalex91-lang/parentbot.git /opt/parentbot && cd /opt/parentbot`
2. Распаковать архив в `/tmp`, скопировать:
   - `parentbot.db` → `/opt/parentbot/db/parentbot.db`
   - `chroma/` → `/opt/parentbot/data/chroma/`
   - `shared_kb/`, `user_kb/`, `announcements/` → `/opt/parentbot/data/`
   - `.env` → `/opt/parentbot/.env` (при необходимости поправить `DATA_DIR`, `DB_PATH`, `CHROMA_DIR`)
3. `python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. Проверить: `.venv/bin/python -c "import sqlite3; print(sqlite3.connect('db/parentbot.db').execute('PRAGMA integrity_check').fetchone())"`
5. `parentbot.service` адаптировать под нового пользователя/пути, положить в `/etc/systemd/system/`, `systemctl enable --now parentbot`

## Защита бэкапа

- `_backup_brainserver_*/` добавлено в `.gitignore` — НЕ коммитится (содержит .env и личные данные пользователей)
- При длительном хранении залить в приватный backup-репо или S3-совместимое хранилище
- Перед удалением каталога — проверить, что есть копия как минимум в одном внешнем месте
