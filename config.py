from dataclasses import dataclass, field
from typing import List
import os
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

    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        admin_telegram_id=admin_id,
        whitelist_ids=whitelist_ids,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        brave_api_key=os.getenv("BRAVE_API_KEY", ""),
        data_dir=os.getenv("DATA_DIR", "data"),
        db_path=os.getenv("DB_PATH", "db/parentbot.db"),
        chroma_dir=os.getenv("CHROMA_DIR", "data/chroma"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "20")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
