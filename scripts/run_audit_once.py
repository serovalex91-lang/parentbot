#!/usr/bin/env python3
"""Однократный прогон weekly audit для backfill-чистки существующих профилей.

Регулярно audit запускается scheduler-ом по понедельникам 04:00 UTC.
Этот скрипт нужен для разового выполнения сразу после миграции, чтобы
не ждать первого понедельника.

Usage (на brain):
    cd /home/brain/projects/parentbot && source .venv/bin/activate
    python3 -u scripts/run_audit_once.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import load_config
from db.schema import init_db
import db.queries as db_queries
from services.scheduler import _weekly_context_audit
from services.claude_client import init_claude
from kb.embedder import warmup


async def main():
    c = load_config()
    db_queries.set_db_path(c.db_path)
    await init_db(c.db_path, c.admin_telegram_id, c.whitelist_ids)
    init_claude(c.openrouter_api_key)
    warmup()
    print("=== AUDIT START ===", flush=True)
    await _weekly_context_audit()
    print("=== AUDIT DONE ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
