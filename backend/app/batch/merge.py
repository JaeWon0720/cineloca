"""정규화·병합 (FR-602~604). PoC p2_analyze.py의 규칙을 그대로 옮겼다.

- 작품·장소는 정규화한 이름(norm)으로 같은 것을 하나로 합친다.
- 촬영 기록은 (작품, 장소, 출처) 하나. 두 출처가 같은 쌍을 주면 기록은 출처별로 남는다.
- 주소·개봉일은 먼저 나온 값이 비어 있으면 뒤의 값으로 채운다.
"""

import re
from dataclasses import dataclass, field

DISTRICTS = (
    "중구",
    "서구",
    "동구",
    "영도구",
    "부산진구",
    "동래구",
    "남구",
    "북구",
    "해운대구",
    "사하구",
    "금정구",
    "강서구",
    "연제구",
    "수영구",
    "사상구",
    "기장군",
)
_BRACKETS = re.compile(r"\(.*?\)|\[.*?\]")
_PUNCT = re.compile(r"[\s·,.:;'\"「」『』<>《》!?~\-–—_/]")
_YEAR = re.compile(r"(19|20)\d\d")


def norm(s: str | None) -> str:
    """작품명·장소명 비교용: 괄호 안 부가설명·공백·문장부호를 빼고 소문자로."""
    s = _BRACKETS.sub("", (s or "").strip())
    return _PUNCT.sub("", s).lower()


def district_of(addr: str | None) -> str | None:
    """주소에서 부산 구·군. 부산이 아니면 None."""
    if not addr:
        return None
    a = addr.replace("부산광역시", "부산 ").replace("부산시", "부산 ")
    if "부산" not in a:
        return None
    for d in sorted(DISTRICTS, key=len, reverse=True):
        if re.search(rf"(^|\s){d}(\s|$)", a):
            return d
    return None


def year_of(s: str | None) -> int | None:
    m = _YEAR.search(s or "")
    return int(m.group(0)) if m else None


@dataclass
class Work:
    key: str
    title: str
    source_id: str
    release: str | None = None

    @property
    def release_year(self) -> int | None:
        return year_of(self.release)


@dataclass
class Place:
    key: str
    name: str
    address: str | None = None

    @property
    def gu(self) -> str | None:
        return district_of(self.address)


@dataclass
class Shooting:
    work_key: str
    place_key: str
    source_id: str
    raw: dict


@dataclass
class Merged:
    works: dict[str, Work] = field(default_factory=dict)
    places: dict[str, Place] = field(default_factory=dict)
    shootings: dict[tuple[str, str, str], Shooting] = field(default_factory=dict)


def merge(rows_by_source: dict[str, list[dict]]) -> Merged:
    """{'DATA-01': [원본 행...], 'DATA-02': [...]} → 작품·장소·촬영 기록."""
    m = Merged()
    for source_id, rows in rows_by_source.items():
        for r in rows:
            title, place = (r.get("opusNm") or "").strip(), (r.get("plcNm") or "").strip()
            wk, pk = norm(title), norm(place)
            if not wk or not pk:
                continue
            w = m.works.setdefault(wk, Work(wk, title, source_id))
            w.release = w.release or (r.get("opnDay") or "").strip() or None
            p = m.places.setdefault(pk, Place(pk, place))
            p.address = p.address or (r.get("roadNmAddr") or "").strip() or None
            m.shootings.setdefault((wk, pk, source_id), Shooting(wk, pk, source_id, r))
    return m
