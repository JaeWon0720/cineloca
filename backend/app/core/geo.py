"""위경도 거리 계산 (하버사인). 업무 서비스(F4 코스·F5 방문 인증)가 함께 쓴다."""

import math

EARTH_RADIUS_M = 6_371_008.8  # 지구 평균 반지름(IUGG)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))
