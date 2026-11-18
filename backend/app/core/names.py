"""이름 정규화 (FR-602). work.title_norm·location.name_norm을 만드는 규칙이다.

배치(병합)와 조회(F1 작품 검색, F4 EntityResolver)가 같은 함수를 써야 같은 이름이 같은 키가 된다.
PoC p2_analyze.py의 규칙을 그대로 옮겼다.

방송사·OTT 머리말('JTBC ', '넷플릭스 ')을 떼는 규칙도 KMDb 매칭(배치)과 F4 사전이 같이 쓴다.
"""

import re

_BRACKETS = re.compile(r"\(.*?\)|\[.*?\]")
_PUNCT = re.compile(r"[\s·,.:;'\"「」『』<>《》!?~\-–—_/]")


def norm(s: str | None) -> str:
    """작품명·장소명 비교용: 괄호 안 부가설명·공백·문장부호를 빼고 소문자로."""
    s = _BRACKETS.sub("", (s or "").strip())
    return _PUNCT.sub("", s).lower()


def station_label(name: str) -> str:
    """역 이름을 화면에 쓰는 꼴로: '서면' → '서면역', '벡스코(시립미술관)' → '벡스코역'."""
    bare = _BRACKETS.sub("", name).strip() or name
    return bare if bare.endswith("역") else bare + "역"


TV_PREFIXES = ("JTBC", "KBS", "MBC", "SBS", "tvN", "OCN", "ENA", "EBS", "TV조선", "MBN", "채널A")
OTT_PREFIXES = (
    "넷플릭스", "디즈니+", "티빙", "왓챠", "쿠팡플레이", "카카오TV", "애플TV+", "아마존 프라임",
    "웨이브", "시즌",
)  # fmt: skip
MEDIA_PREFIXES = TV_PREFIXES + OTT_PREFIXES
_MEDIA = re.compile(r"^(" + "|".join(re.escape(m) for m in MEDIA_PREFIXES) + r")\s+(.+)$")


def split_media(title: str) -> tuple[str, str | None]:
    """'JTBC 나의 해방일지' → ('나의 해방일지', 'JTBC'). 머리말이 없으면 (title, None)."""
    m = _MEDIA.match(" ".join(title.split()))
    return (m.group(2), m.group(1)) if m else (title, None)
