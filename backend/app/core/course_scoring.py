"""AI 맞춤 코스 후보 점수 설정 (FR-309: 가중치는 설정 파일로 관리).

점수 = Σ 가중치 × 항목 점수(각 0~1). 가중치 합은 1이다.
  match      : 취향 일치. 문장에서 정한 항목(태그·장르·작품·인물·구·군) 중 맞은 비율
  similarity : 취향 문장 임베딩과 촬영지 임베딩의 코사인 유사도(bge-m3, FR-617). 0 미만은 0
  works      : 촬영 작품 수(WORKS_CAP편에서 1)
  distance   : 출발지에서 가까울수록 1, MAX_DISTANCE_KM 이상이면 0(직선거리)
값을 바꾸면 F3 평가(AI-REQ-11)를 다시 돌린다.
"""

WEIGHTS: dict[str, float] = {
    "match": 0.45,
    "similarity": 0.25,
    "works": 0.10,
    "distance": 0.20,
}
WORKS_CAP = 5
MAX_DISTANCE_KM = 8.0
TOP_K = 15  # FR-309 상위 15곳
DEFAULT_BUDGET_H = 4.0  # FR-307 기본 4시간
