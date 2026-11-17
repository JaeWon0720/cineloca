"""환경변수 설정 (NFR-009: 비밀값은 환경변수로만 주입한다)."""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    log_dir: str | None = None  # 요청 로그 폴더(NFR-005). 비우면 파일로 남기지 않는다
    log_level: str = "INFO"  # app 로거 수준. 콘솔이 시끄러우면 WARNING


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL") or None,
        log_dir=os.getenv("LOG_DIR") or None,
        log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
    )
