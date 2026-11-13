"""Privacy Guard (COMP-10): 입력 마스킹과 로그 정제.

- mask(text): 외부 AI로 보내기 전 전화번호·이메일·주민등록번호·카드번호를
  '[개인정보]'로 바꾼다 (FR-702, AI-REQ-03).
- scrub(record): 로그로 남길 레코드에서 위치 좌표·비밀값을 빼고 문자열을 마스킹한다
  (NFR-003, NFR-005, NFR-009).
- PrivacyLogFilter: 로그 메시지에 남은 개인정보 패턴과 접근 로그의 클라이언트 IP를 가린다.
- install_privacy_log_filter(): 서버 시작 시 root·uvicorn 로그 핸들러에 필터를 붙인다.

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

# 숫자 묶음 사이 구분자: '-', '.', 공백. 하이픈 앞뒤 공백도 허용한다(예: 010 - 1234 - 5678).
_SEP = r"\s*[-.]?\s*"

# 개인정보를 너무 적게 가리는 것보다 조금 넓게 가리는 쪽을 택한다(개인정보 우선).
# 순서가 중요하다: 긴 패턴(이메일·주민번호·카드)을 먼저 바꿔야
# 전화번호 패턴이 그 일부를 잘못 잡지 않는다.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # 주민등록번호: 생년월일 6자리(월 01~12, 일 01~31) + 성별 1~8 + 6자리
    (
        "rrn",
        re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\s*-?\s*[1-8]\d{6}(?!\d)"),
    ),
    # 카드번호: 4-4-4-4(16자리) 또는 4-6-5(15자리). 묶음마다 구분자가 달라도 가린다.
    (
        "card",
        re.compile(
            rf"(?<!\d)(?:\d{{4}}{_SEP}\d{{4}}{_SEP}\d{{4}}{_SEP}\d{{4}}"
            rf"|\d{{4}}{_SEP}\d{{6}}{_SEP}\d{{5}})(?!\d)"
        ),
    ),
    # 전화번호: 휴대폰(010 등), 지역번호(02, 051 등), 070, +82 국가번호
    (
        "phone",
        re.compile(
            rf"(?<![\d+])(?:\+82{_SEP}(?:1[016789]|[2-6]\d?|70)|01[016789]|0[2-6]\d?|070)"
            rf"{_SEP}\d{{3,4}}{_SEP}\d{{4}}(?!\d)"
        ),
    ),
)

# 키 이름은 단어로 나눠서 비교한다. 예: GEMINI_API_KEY → gemini/api/key, serviceKey → service/key
# (부분 문자열로 비교하면 latency_ms의 'lat', template의 'lat'까지 지워져서 단어 단위로 본다)
_SECRET_WORDS = frozenset({"password", "passwd", "authorization", "cookie", "secret", "token"})
_SECRET_SUFFIXES = ("key", "token", "secret", "password", "passwd")  # apikey, servicekey …
_LOCATION_WORDS = frozenset(
    {
        "lat",
        "lon",
        "lng",
        "latitude",
        "longitude",
        "coord",
        "coords",
        "coordinate",
        "coordinates",
        "accuracy",
    }
)
_LOCATION_NAMES = frozenset({"x", "y", "mapx", "mapy"})  # 카카오·TourAPI 좌표 이름

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def _words(name: str) -> list[str]:
    return [w.lower() for w in _WORD_SPLIT.split(name) if w]


def _is_secret_key(name: str) -> bool:
    return any(w in _SECRET_WORDS or w.endswith(_SECRET_SUFFIXES) for w in _words(name))


def _is_location_key(name: str) -> bool:
    flat = re.sub(r"[^a-z0-9]", "", name.lower())
    return flat in _LOCATION_NAMES or any(w in _LOCATION_WORDS for w in _words(name))


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

    - 위치 키(lat, user_lat, mapX, coords …)와
      비밀값 키(GEMINI_API_KEY, access_token …)의 값은 '[제거됨]'
    - 모든 문자열은 mask() 적용
    - dict·list·tuple은 안쪽까지 같은 규칙 적용
    """
    if isinstance(record, str):
        return mask(record).text
    if isinstance(record, Mapping):
        cleaned: dict[Any, Any] = {}
        for key, value in record.items():
            name = str(key)
            if _is_location_key(name) or _is_secret_key(name):
                cleaned[key] = REMOVED
            else:
                cleaned[key] = scrub(value)
        return cleaned
    if isinstance(record, (list, tuple)):
        return type(record)(scrub(item) for item in record)
    return record


_EXC_FORMATTER = logging.Formatter()


class PrivacyLogFilter(logging.Filter):
    """로그 레코드를 기록 직전에 정제한다(NFR-005: 마스킹된 형태로 기록).

    - 메시지와 인자의 개인정보 패턴을 가린다.
    - uvicorn 접근 로그의 첫 인자(클라이언트 주소)는 남기지 않는다(NFR-005 로그 항목에 IP 없음).
    - uvicorn 접근 로그의 경로에서 쿼리 문자열을 지운다.
    - 스택 추적(예외 메시지 포함)의 개인정보 패턴을 가린다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask(record.msg).text
        if isinstance(record.args, Mapping):
            record.args = scrub(record.args)
        elif isinstance(record.args, tuple):
            args = tuple(scrub(arg) for arg in record.args)
            if record.name == "uvicorn.access" and args:
                args = (REMOVED, *args[1:])
                # 접근 로그에는 경로만 남기고 쿼리 문자열은 지운다(SDD 8장 로그 정책)
                if len(args) > 2 and isinstance(args[2], str):
                    args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
            record.args = args
        if record.exc_info and not record.exc_text:
            # 스택 추적의 예외 메시지에도 개인정보가 있을 수 있어 가린 문장을 미리 넣어 둔다
            record.exc_text = mask(_EXC_FORMATTER.formatException(record.exc_info)).text
        elif record.exc_text:
            record.exc_text = mask(record.exc_text).text
        return True


class _PrivacyRootHandler(logging.StreamHandler):
    """root 로거에 핸들러가 없을 때 붙이는 출력 핸들러(앱 로거 로그가 필터를 거치게 한다)."""


def _uvicorn_like_formatter() -> logging.Formatter:
    try:
        from uvicorn.logging import DefaultFormatter

        return DefaultFormatter("%(levelprefix)s %(message)s", use_colors=None)
    except ImportError:  # uvicorn 없이 실행할 때
        return logging.Formatter("%(levelname)s:  %(message)s")


def install_privacy_log_filter(
    logger_names: Iterable[str] = ("", "uvicorn", "uvicorn.error", "uvicorn.access"),
) -> None:
    """지정한 로거들의 핸들러에 PrivacyLogFilter를 붙인다. 여러 번 불러도 한 번만 붙는다.

    uvicorn 기본 설정에서는 root 로거에 핸들러가 없어서 logging.getLogger("app.xxx")로
    남긴 로그가 필터를 거치지 않는다. 그래서 root에 핸들러가 없으면
    uvicorn과 같은 형식의 핸들러를 붙인다.
    """
    names = list(logger_names)
    root = logging.getLogger()
    if "" in names and not root.handlers:
        handler = _PrivacyRootHandler()
        handler.setFormatter(_uvicorn_like_formatter())
        root.addHandler(handler)

    log_filter = PrivacyLogFilter()
    for name in names:
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, PrivacyLogFilter) for f in handler.filters):
                handler.addFilter(log_filter)
