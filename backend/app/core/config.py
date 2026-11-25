"""환경변수 설정 (NFR-009: 비밀값은 환경변수로만 주입한다)."""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    log_dir: str | None = None  # 요청 로그 폴더(NFR-005). 비우면 파일로 남기지 않는다
    log_level: str = "INFO"  # app 로거 수준. 콘솔이 시끄러우면 WARNING
    # C1 배치: 주소 없는·동까지만 있는 촬영지를 카카오 키워드 검색으로 찾을지(#25).
    # 카카오 약관 확인 전까지 좌표를 저장하지 않으므로 기본은 끈다(ADR-006).
    geocode_kakao_keyword: bool = False
    stamp_signing_key: str | None = None  # 스탬프·배지 서명 키(ADR-009). 비우면 F5만 503
    tour_api_key: str | None = (
        None  # 공공데이터포털 키(TourAPI 주변 시설, FR-414). 비우면 시설 검색만 실패
    )
    admin_password_hash: str | None = None  # 관리자 비밀번호 해시(FR-615). 비우면 관리 API만 503
    sync_interval_days: int = 7  # 정기 동기화 주기(FR-616). 0이면 끔. 배치(--watch)와 같은 값


def _days(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    return int(raw) if raw.isdigit() else default


def _flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL") or None,
        log_dir=os.getenv("LOG_DIR") or None,
        log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
        geocode_kakao_keyword=_flag("GEOCODE_KAKAO_KEYWORD"),
        stamp_signing_key=os.getenv("STAMP_SIGNING_KEY") or None,
        tour_api_key=os.getenv("DATA_GO_KR_SERVICE_KEY") or None,
        admin_password_hash=(os.getenv("ADMIN_PASSWORD_HASH") or "").strip() or None,
        sync_interval_days=_days("SYNC_INTERVAL_DAYS", 7),
    )
