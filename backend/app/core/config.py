"""환경변수 설정 (NFR-009: 비밀값은 환경변수로만 주입한다)."""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    database_url: str | None


@lru_cache
def get_settings() -> Settings:
    return Settings(database_url=os.getenv("DATABASE_URL") or None)
