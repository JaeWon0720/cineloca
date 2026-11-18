"""이름 정규화 (FR-602). work.title_norm·location.name_norm을 만드는 규칙이다.

배치(병합)와 조회(F1 작품 검색, F4 EntityResolver)가 같은 함수를 써야 같은 이름이 같은 키가 된다.
PoC p2_analyze.py의 규칙을 그대로 옮겼다.
"""

import re

_BRACKETS = re.compile(r"\(.*?\)|\[.*?\]")
_PUNCT = re.compile(r"[\s·,.:;'\"「」『』<>《》!?~\-–—_/]")


def norm(s: str | None) -> str:
    """작품명·장소명 비교용: 괄호 안 부가설명·공백·문장부호를 빼고 소문자로."""
    s = _BRACKETS.sub("", (s or "").strip())
    return _PUNCT.sub("", s).lower()
