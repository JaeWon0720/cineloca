"""코스 주변 시설 추천 설정 (F4 FR-414~418, CR-01).

시설 분류는 이 목록 안 값만 쓴다(AI 조건 구조화의 허용 값, 화면의 직접 고르기 선택지).
후보(FR-414, app/core/amenities.py의 category_of)와 같은 이름을 쓴다. 분류를 모르는 후보는
None으로 오고, 분류 조건에는 안 맞은 것으로 치고 거리 점수로만 순위를 매긴다.
점수 = Σ 가중치 × 항목 점수(각 0~1). 가중치는 이 설정 파일로 관리한다(FR-416).
값을 바꾸면 시설 추천 평가를 다시 돌린다.
"""

from typing import Literal

FacilityKind = Literal["food", "lodging"]
FoodCategory = Literal["밥", "카페"]
LodgingCategory = Literal[
    "호텔", "콘도·레지던스", "펜션·민박", "모텔", "캠핑", "게스트하우스·호스텔"
]

FOOD_CATEGORIES: tuple[str, ...] = ("밥", "카페")
LODGING_CATEGORIES: tuple[str, ...] = (
    "호텔",
    "콘도·레지던스",
    "펜션·민박",
    "모텔",
    "캠핑",
    "게스트하우스·호스텔",
)
KIND_LABELS = {"food": "음식", "lodging": "숙박"}

WEIGHTS: dict[str, float] = {"match": 0.6, "distance": 0.4}
SEARCH_RADIUS_M = 1000  # FR-414: 구간 중간 지점·마지막 촬영지에서 1km
PER_SEGMENT = 3  # FR-416: 구간마다 최대 3곳

# 추천 이유(FR-417)에 들어가면 그 문장을 지우는 말. 후보 데이터(TourAPI 위치기반 조회)에는
# 맛·가격·인기·청결 같은 평가가 없어서 이런 말은 근거 없이 지어낸 것이다.
UNVERIFIABLE_PHRASES: tuple[str, ...] = (
    "맛집",
    "맛있",
    "유명",
    "인기",
    "최고",
    "추천 1",
    "저렴",
    "가성비",
    "평점",
    "리뷰",
    "친절",
    "깨끗",
    "청결",
    "분위기 좋",
    "줄 서",
)
