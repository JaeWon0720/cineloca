"""KOBIS 관람등급 클라이언트 테스트 (FR-107, ERR-006). 실제 KOBIS는 부르지 않는다.

응답은 손으로 만든 가짜다(KOBIS 응답 본문은 저장 금지라 실제 사본을 두지 않는다).
"""

import asyncio

import httpx
import pytest

from app.data.kobis import KobisRatingProvider, RatingUnavailable, parse_rating

FAKE_KEY = "fake-kobis-key"


def info(audits: list[dict] | None, movie_cd: str | None = "20990001") -> dict:
    return {"movieInfoResult": {"movieInfo": {"movieCd": movie_cd, "audits": audits}}}


def rating_with(handler) -> str | None:
    provider = KobisRatingProvider(FAKE_KEY, transport=httpx.MockTransport(handler))
    return asyncio.run(provider.rating("20990001", 3.0))


def test_rating_sends_code_and_returns_latest_grade() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        audits = [{"watchGradeNm": "12세이상관람가"}, {"watchGradeNm": "15세이상관람가"}]
        return httpx.Response(200, json=info(audits))

    assert rating_with(handler) == "15세이상관람가"  # 재심의면 마지막 결과
    assert seen[0].url.params["movieCd"] == "20990001"
    assert seen[0].url.params["key"] == FAKE_KEY


@pytest.mark.parametrize("body", [info([]), info(None, movie_cd=None), info([{"auditNo": "1"}])])
def test_no_grade_is_none(body: dict) -> None:
    assert parse_rating(body) is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="oops"),
        httpx.Response(200, text="<html>점검 중</html>"),
        httpx.Response(200, json={"faultInfo": {"errorCode": "320010", "message": "키 오류"}}),
        httpx.Response(200, json={"movieInfoResult": None}),
    ],
)
def test_failures_raise_without_leaking_key_or_body(response: httpx.Response) -> None:
    with pytest.raises(RatingUnavailable) as e:
        rating_with(lambda _req: response)

    assert FAKE_KEY not in str(e.value) and "키 오류" not in str(e.value)
    assert e.value.__cause__ is None  # 키가 든 URL을 가진 원래 예외를 붙이지 않는다


def test_timeout_raises() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=req)

    with pytest.raises(RatingUnavailable, match="ReadTimeout"):
        rating_with(handler)
