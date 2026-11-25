"""부하 테스트 스크립트(NFR-001·006) 테스트. 실제 서버 대신 httpx.MockTransport로 응답한다."""

import asyncio
import json
import random

import httpx

from scripts.load_test import (
    TARGET_GENERAL_MS,
    Sample,
    ai_jobs,
    discover,
    p95,
    run_ai,
    run_general,
    summarize,
)


def test_p95_nearest_rank():
    assert p95([]) is None
    assert p95([5]) == 5
    assert p95(list(range(1, 101))) == 95  # round(0.95 * 99) = 94번째(0부터)


def test_summarize_counts_errors_and_targets():
    samples = [Sample("A", 100, 200), Sample("A", 2500, 200), Sample("B", 50, 500)]
    s = summarize(samples, TARGET_GENERAL_MS)
    assert s["apis"]["A"]["p95_ms"] == 2500 and s["apis"]["A"]["pass"] is False
    assert s["apis"]["B"]["errors"] == 1 and s["pass"] is False
    ok = summarize([Sample("A", 10, 200, "ok")], TARGET_GENERAL_MS)
    assert ok["pass"] is True and ok["apis"]["A"]["outcomes"] == {"ok": 1}
    degraded = summarize([Sample("A", 10, 200, "degraded")], TARGET_GENERAL_MS)
    assert degraded["apis"]["A"]["degraded"] == 1 and degraded["pass"] is False


def _handler(request: httpx.Request) -> httpx.Response:
    path, method = request.url.path, request.method
    if path == "/api/works" and method == "GET":
        items = [{"id": 1, "kmdb_matched": True}, {"id": 2, "kmdb_matched": False}]
        return httpx.Response(200, json={"items": items})
    if path == "/api/courses":
        return httpx.Response(200, json={"items": [{"id": 7}]})
    if path == "/api/courses/7":
        return httpx.Response(200, json={"stops": [{"id": 11}, {"id": 12}, {"id": 13}]})
    if path == "/api/locations/search":
        return httpx.Response(200, json={"items": [{"id": 11}, {"id": 12}, {"id": 13}]})
    if path.endswith("/intro") or path == "/api/query":
        assert request.headers["X-Notice-Version"]
        return httpx.Response(200, json={"status": "ok"})
    if path == "/api/courses/amenities":
        body = json.loads(request.content)
        assert body["location_ids"] == [11, 12, 13]
        return httpx.Response(200, json={"conditions": {"status": "ai"}})
    return httpx.Response(200, json={})


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(_handler))


def test_discover_and_general_run():
    async def go():
        async with _client() as c:
            inp = await discover(c)
            samples = await run_general(c, inp, users=2, duration_s=0.05, seed=1)
            return inp, samples

    inp, samples = asyncio.run(go())
    assert inp.intro_work_ids == [1] and inp.course_stops == [[11, 12, 13]]
    assert samples and all(s.status == 200 for s in samples)
    assert summarize(samples, TARGET_GENERAL_MS)["pass"] is True


def test_ai_jobs_are_distinct_and_run():
    async def go():
        async with _client() as c:
            inp = await discover(c)
            jobs = ai_jobs(inp, per_api=3, rnd=random.Random(1))
            samples = await run_ai(c, inp, users=4, per_api=3, seed=1)
            return jobs, samples

    jobs, samples = asyncio.run(go())
    # 소개 대상 작품 1편 × 스포일러 2단계 = 2개, 질의 3개, 시설 3개
    assert len(jobs) == 8 and len(samples) == 8
    apis = {s.api for s in samples}
    assert apis == {"API-004 AI 작품 소개", "API-013 AI 대화형 탐색", "API-012 주변 시설 추천"}
