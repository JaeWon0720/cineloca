"""Privacy Guard (COMP-10): 입력 마스킹과 로그 정제.

- mask(text): 외부 AI로 보내기 전 전화번호·이메일·주민등록번호·카드번호를
  '[개인정보]'로 바꾼다 (FR-702, AI-REQ-03).
- scrub(record): 로그로 남길 레코드에서 위치 좌표·비밀값을 빼고 문자열을 마스킹한다
  (NFR-003, NFR-005, NFR-009).
- PrivacyLogFilter: 로그 메시지에 남은 개인정보 패턴을 기록 직전에 가린다.

외부 라이브러리 없이 동작하는 순수 모듈이다(SDD 3.3 시험 용이성).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

PLACEHOLDER = "[개인정보]"
REMOVED = "[제거됨]"
MASK_NOTICE = "개인정보로 보이는 내용을 가렸습니다"  # 사용자 안내 문구 (SDD 오류·예외 표)

# 개인정보를 너무 적게 가리는 것보다 조금 넓게 가리는 쪽을 택한다(개인정보 우선).
# 순서가 중요하다: 긴 패턴(이메일·주민번호·카드)을 먼저 바꿔야
# 전화번호 패턴이 그 일부를 잘못 잡지 않는다.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # 주민등록번호: 생년월일 6자리(월 01~12, 일 01~31) + 성별 1~8 + 6자리
    (
        "rrn",
        re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[-\s]?[1-8]\d{6}(?!\d)"),
    ),
    # 카드번호: 4-4-4-4(16자리) 또는 4-6-5(15자리), 구분자는 '-'나 공백
    (
        "card",
        re.compile(r"(?<!\d)(?:\d{4}([-\s]?)\d{4}\1\d{4}\1\d{4}|\d{4}([-\s]?)\d{6}\2\d{5})(?!\d)"),
    ),
    # 전화번호: 휴대폰(010 등), 지역번호(02, 051 등), 070, +82 국가번호
    (
        "phone",
        re.compile(
            r"(?<![\d+])(?:\+82[-.\s]?(?:1[016789]|[2-6]\d?|70)|01[016789]|0[2-6]\d?|070)"
            r"[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"
        ),
    ),
)

# 로그에 값을 남기지 않을 키 (소문자로 비교)
_LOCATION_KEYS = frozenset(
    {"lat", "lon", "lng", "latitude", "longitude", "coords", "coordinates", "accuracy"}
)
_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "authorization",
        "cookie",
        "set-cookie",
        "token",
        "api_key",
        "apikey",
        "servicekey",
        "secret",
    }
)


@dataclass(frozen=True)
class MaskedText:
    """마스킹 결과. kinds에는 가린 종류만 남긴다(원문 값은 남기지 않음)."""

    text: str
    kinds: tuple[str, ...] = ()

    @property
    def masked(self) -> bool:
        return bool(self.kinds)


def mask(text: str) -> MaskedText:
    """개인정보 패턴을 '[개인정보]'로 바꾼다. 바꾼 종류는 처음 나온 순서대로 한 번씩 기록한다."""
    kinds: list[str] = []
    for kind, pattern in _PATTERNS:
        text, count = pattern.subn(PLACEHOLDER, text)
        if count and kind not in kinds:
            kinds.append(kind)
    return MaskedText(text=text, kinds=tuple(kinds))


def scrub(record: Any) -> Any:
    """로그용 레코드를 정제한 복사본을 돌려준다. 원본은 바꾸지 않는다.

    - 위치 좌표 키(lat, lon …)와 비밀값 키(password, token …)의 값은 '[제거됨]'
    - 모든 문자열은 mask() 적용
    - dict·list·tuple은 안쪽까지 같은 규칙 적용
    """
    if isinstance(record, str):
        return mask(record).text
    if isinstance(record, Mapping):
        cleaned: dict[Any, Any] = {}
        for key, value in record.items():
            name = str(key).lower()
            if name in _LOCATION_KEYS or name in _SECRET_KEYS:
                cleaned[key] = REMOVED
            else:
                cleaned[key] = scrub(value)
        return cleaned
    if isinstance(record, (list, tuple)):
        return type(record)(scrub(item) for item in record)
    return record


class PrivacyLogFilter(logging.Filter):
    """로그 레코드의 메시지와 인자에 남은 개인정보 패턴을 가린다(NFR-005: 마스킹된 형태로 기록)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask(record.msg).text
        if isinstance(record.args, Mapping):
            record.args = scrub(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(scrub(arg) for arg in record.args)
        return True


def install_privacy_log_filter(
    logger_names: Iterable[str] = ("", "uvicorn", "uvicorn.error", "uvicorn.access"),
) -> None:
    """지정한 로거들의 핸들러에 PrivacyLogFilter를 붙인다. 여러 번 불러도 한 번만 붙는다."""
    log_filter = PrivacyLogFilter()
    for name in logger_names:
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, PrivacyLogFilter) for f in handler.filters):
                handler.addFilter(log_filter)
