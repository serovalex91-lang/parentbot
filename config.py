from dataclasses import dataclass
from typing import List
import os
import sys
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    bot_token: str
    admin_telegram_id: int
    whitelist_ids: List[int]
    anthropic_api_key: str
    brave_api_key: str
    data_dir: str
    db_path: str
    chroma_dir: str
    claude_model: str
    claude_model_light: str
    max_history_messages: int
    log_level: str

    @property
    def shared_kb_dir(self) -> str:
        return os.path.join(self.data_dir, "shared_kb")

    @property
    def user_kb_dir(self) -> str:
        return os.path.join(self.data_dir, "user_kb")


def load_config() -> Config:
    whitelist_raw = os.getenv("WHITELIST_IDS", "")
    whitelist_ids = [int(x.strip()) for x in whitelist_raw.split(",") if x.strip()]

    admin_id = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
    if admin_id and admin_id not in whitelist_ids:
        whitelist_ids.append(admin_id)

    bot_token = os.getenv("BOT_TOKEN", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    missing = []
    if not bot_token:
        missing.append("BOT_TOKEN")
    if not anthropic_key:
        missing.append("ANTHROPIC_API_KEY")
    if not admin_id:
        missing.append("ADMIN_TELEGRAM_ID")
    if missing:
        print(f"ОШИБКА: не заданы обязательные переменные окружения: {', '.join(missing)}", file=sys.stderr)
        print("Проверь файл .env", file=sys.stderr)
        sys.exit(1)

    return Config(
        bot_token=bot_token,
        admin_telegram_id=admin_id,
        whitelist_ids=whitelist_ids,
        anthropic_api_key=anthropic_key,
        brave_api_key=os.getenv("BRAVE_API_KEY", ""),
        data_dir=os.getenv("DATA_DIR", "data"),
        db_path=os.getenv("DB_PATH", "db/parentbot.db"),
        chroma_dir=os.getenv("CHROMA_DIR", "data/chroma"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        claude_model_light=os.getenv("CLAUDE_MODEL_LIGHT", "claude-haiku-4-5-20251001"),
        max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "20")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
