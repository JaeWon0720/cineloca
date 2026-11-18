"""KOBIS 관람등급 실시간 조회 (COMP-11 RatingProvider, API-003, FR-107, ADR-007).

KOBIS는 실시간 사용만 허용한다. 응답 본문은 DB·캐시·파일·로그·픽스처 어디에도 남기지 않고
등급 문자열 하나만 꺼내 돌려준다. 키가 든 URL과 응답 내용은 예외 메시지에 넣지 않는다.
상세 화면을 막지 않게 재시도 없이 한 번만 부른다(제한 시간은 부르는 쪽이 정한다).
"""

from __future__ import annotations

import httpx

INFO_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/movie/searchMovieInfo.json"


class RatingUnavailable(Exception):
    """KOBIS 호출 실패·시간 초과·형식 오류(ERR-006). 메시지는 오류 종류뿐이다."""


class KobisRatingProvider:
    def __init__(self, key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = key
        self._transport = transport  # 테스트는 httpx.MockTransport를 넣는다

    async def rating(self, movie_cd: str, timeout_s: float) -> str | None:
        """영화 코드의 현재 관람등급. KOBIS에 없는 코드이거나 심의 정보가 없으면 None."""
        params = {"key": self._key, "movieCd": movie_cd}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=timeout_s) as http:
                r = await http.get(INFO_URL, params=params)
            if r.status_code != 200:
                raise RatingUnavailable(f"HTTP {r.status_code}")
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise RatingUnavailable(type(e).__name__) from None
        return parse_rating(body)


def parse_rating(body: object) -> str | None:
    if not isinstance(body, dict) or "faultInfo" in body:  # 잘못된 키·한도 초과는 200 + faultInfo
        raise RatingUnavailable("fault")
    try:
        info = body["movieInfoResult"]["movieInfo"]
        audits = info.get("audits") or []
        # 재심의로 여러 건이면 마지막 심의 결과가 현재 등급이다
        grade = audits[-1].get("watchGradeNm") if audits else None
    except (KeyError, TypeError, AttributeError, IndexError):
        raise RatingUnavailable("format") from None
    return grade.strip() or None if isinstance(grade, str) else None
