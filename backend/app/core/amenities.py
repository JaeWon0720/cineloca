"""주변 시설 분류 (FR-415 허용 값, SDD 2.3, CHANGES 11/9).

TourAPI 신분류(lclsSystm) 코드를 서비스 분류로 바꾼다. 분류 이름은 app/core/facilities.py에서
가져와 조건 구조화(FR-415)의 허용 값과 늘 같다.
- 음식(contentTypeId 39): 밥 = FD01·FD02·FD03(제과 FD030100 제외), 카페 = FD05 + 제과 FD030100.
  주점 FD04는 부산 데이터에 없어 받지 않는다.
- 숙박(contentTypeId 32): AC01~AC06.
"""

from app.core.facilities import FOOD_CATEGORIES, LODGING_CATEGORIES, FacilityKind

FacilityType = FacilityKind  # 분류 이름 목록은 app/core/facilities.py 하나만 쓴다(#88)

CONTENT_TYPE: dict[FacilityType, int] = {"food": 39, "lodging": 32}

__all__ = [
    "CONTENT_TYPE",
    "EXCLUDED_PREFIXES",
    "FOOD_CATEGORIES",
    "LODGING_CATEGORIES",
    "FacilityType",
    "category_of",
    "is_excluded",
]

_LODGING_BY_PREFIX = dict(
    zip(("AC01", "AC02", "AC03", "AC04", "AC05", "AC06"), LODGING_CATEGORIES, strict=True)
)
EXCLUDED_PREFIXES = ("FD04",)  # 주점


def category_of(facility: FacilityType, lcls_code: str | None) -> str | None:
    """lclsSystm3(또는 2) 코드 → 서비스 분류. 모르면 None(분류 조건에는 안 맞고 거리로만 선정)."""
    code = (lcls_code or "").strip().upper()
    if facility == "food":
        if code.startswith("FD05") or code.startswith("FD030100"):
            return "카페"
        if code.startswith(("FD01", "FD02", "FD03")):
            return "밥"
        return None
    return _LODGING_BY_PREFIX.get(code[:4])


def is_excluded(lcls_code: str | None) -> bool:
    return (lcls_code or "").strip().upper().startswith(EXCLUDED_PREFIXES)
