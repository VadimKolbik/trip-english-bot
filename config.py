import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> list[int]:
    """'111,222' -> [111, 222]; пустая строка -> []."""
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    bot_token: str
    superadmin_ids: list[int] = field(default_factory=list)
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    timezone: str = "Europe/Minsk"


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in .env")
    return Config(
        bot_token=token,
        superadmin_ids=_parse_ids(os.getenv("SUPERADMIN_IDS", "")),
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db"),
        timezone=os.getenv("TZ", "Europe/Minsk"),
    )


config = load_config()