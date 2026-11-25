"""부하 테스트 (NFR-001·006, 테스트 보고서 N-01·N-02). 개발용, 서비스 코드에서 쓰지 않는다.

실행 중인 서비스(docker compose up)에 동시 사용자 N명(기본 4명)의 요청을 보내고 API별 p95를 잰다.
backend 폴더에서:

    python -m scripts.load_test general --duration 300 --out /tmp/load   # NFR-001: p95 ≤ 2초
    python -m scripts.load_test ai --per-api 20 --out /tmp/load          # NFR-006: p95 ≤ 8초

- general: 근접 검색(API-005), 필터 검색(API-014), 작품 상세(API-002), 추천 코스 상세(API-010),
  나만의 코스 경로 계산(API-011)을 섞어 --duration초 동안 보낸다.
- ai: AI 작품 소개(API-004), AI 대화형 탐색(API-013), 주변 시설 추천(API-012)을 API마다 --per-api번,
  매번 다른 입력으로 보낸다. NFR-006은 캐시 미적중 기준이므로 실행 전에 AI 응답 캐시를 비운다:
      docker compose exec db psql -U cineloca -d cineloca -c "TRUNCATE ai_cache"
  Gemini 무료 한도(하루 20회)로는 모자라므로 로컬 모델(AI_PROVIDER=ollama)로 띄운 서비스에서 잰다.
  AI가 실패해 강등된 응답도 응답 시간에 넣고, 강등 수는 따로 센다.
- 대상 주소는 --base(기본 http://127.0.0.1:8080, web 컨테이너의 /api 프록시).
  API 컨테이너에 바로 붙이려면 :8000.
- 입력값(작품·코스·촬영지 ID)은 실행할 때 목록 API에서 가져온다.
  결과 파일에는 API 이름·지연·상태만 남긴다(질의 원문·좌표는 남기지 않음).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.notice import NOTICE_VERSION

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
TARGET_GENERAL_MS = 2000  # NFR-001
TARGET_AI_MS = 8000  # NFR-006
NOTICE = {"X-Notice-Version": NOTICE_VERSION}
# 근접 검색 기준점: 부산의 촬영지가 많은 곳(부산역·해운대·서면·남포·광안리·영도)
POINTS = [
    (35.1151, 129.0414),
    (35.1587, 129.1604),
    (35.1578, 129.0600),
    (35.0979, 129.0300),
    (35.1532, 129.1186),
    (35.0910, 129.0680),
]
DISTRICTS = ["해운대구", "중구", "부산진구", "수영구", "영도구", "동구", "사하구", "남구"]


# ── 집계 ─────────────────────────────────────────────────
def p95(values: Sequence[float]) -> float | None:
    """eval_ai와 같은 nearest-rank p95."""
    xs = sorted(values)
    return xs[round(0.95 * (len(xs) - 1))] if xs else None


@dataclass
class Sample:
    api: str
    ms: float
    status: int  # HTTP 상태. 연결 실패는 0
    outcome: str | None = None  # AI 응답의 status(ok·degraded 등) 또는 ai_generated 여부


@dataclass
class Recorder:
    samples: list[Sample] = field(default_factory=list)

    def add(self, s: Sample) -> None:
        self.samples.append(s)


DEGRADED = {"degraded", "ai_unavailable", "manual"}  # AI 없이 돌려준 응답(F1·F3·F4)


def summarize(samples: Sequence[Sample], target_ms: float) -> dict[str, Any]:
    """API별 건수·오류·p50·p95·최대와 목표 통과 여부. 오류(200 아님)도 지연에 넣는다.

    AI 응답 시간(NFR-006)은 AI가 답한 응답을 재므로 강등 응답이 섞이면 통과로 보지 않는다.
    """
    by_api: dict[str, list[Sample]] = defaultdict(list)
    for s in samples:
        by_api[s.api].append(s)
    apis = {}
    for api, ss in sorted(by_api.items()):
        ms = [s.ms for s in ss]
        apis[api] = {
            "requests": len(ss),
            "errors": sum(1 for s in ss if s.status != 200),
            "status": dict(Counter(str(s.status) for s in ss)),
            "outcomes": dict(Counter(s.outcome for s in ss if s.outcome)),
            "p50_ms": round(sorted(ms)[len(ms) // 2]),
            "p95_ms": round(p95(ms) or 0),
            "max_ms": round(max(ms)),
            "degraded": sum(1 for s in ss if s.outcome in DEGRADED),
            "pass": (p95(ms) or 0) <= target_ms,
        }
    return {
        "target_p95_ms": target_ms,
        "requests": len(samples),
        "pass": bool(apis)
        and all(a["pass"] and a["errors"] == 0 and a["degraded"] == 0 for a in apis.values()),
        "apis": apis,
    }


# ── 입력값 준비 ──────────────────────────────────────────
@dataclass
class Inputs:
    work_ids: list[int]
    intro_work_ids: list[int]  # KMDb에 매칭된 작품(AI 소개 대상)
    course_ids: list[int]
    course_stops: list[
        list[int]
    ]  # 시설 추천에 쓸 촬영지 ID 묶음(추천 코스, 없으면 근처 촬영지 3곳)
    location_ids: list[int]  # 좌표가 있는 촬영지


async def discover(client: httpx.AsyncClient) -> Inputs:
    works = (await client.get("/api/works", params={"size": 100})).json()["items"]
    courses = (await client.get("/api/courses")).json()["items"]
    stops = []
    for c in courses:
        detail = (await client.get(f"/api/courses/{c['id']}")).json()
        stops.append([s["id"] for s in detail.get("stops", [])])
    locations: set[int] = set()
    nearby: list[list[int]] = []
    for lat, lon in POINTS:
        r = await client.post(
            "/api/locations/search", json={"base": {"lat": lat, "lon": lon}, "radius_m": 3000}
        )
        ids = [item["id"] for item in r.json()["items"]]
        locations.update(ids)
        if len(ids) >= 3:
            nearby.append(ids[:3])
    inputs = Inputs(
        work_ids=[w["id"] for w in works],
        intro_work_ids=[w["id"] for w in works if w.get("kmdb_matched")],
        course_ids=[c["id"] for c in courses],
        course_stops=[s for s in stops if len(s) >= 2] or nearby,
        location_ids=sorted(locations),
    )
    if not (inputs.work_ids and inputs.location_ids):
        raise SystemExit("작품·촬영지가 없습니다. 시드를 적재했는지 확인하세요")
    return inputs


# ── 요청 ─────────────────────────────────────────────────
Op = Callable[[httpx.AsyncClient, random.Random], Awaitable[Sample]]


async def timed(
    api: str, call: Awaitable[httpx.Response], outcome: Callable[[Any], str | None] | None = None
) -> Sample:
    started = time.perf_counter()
    try:
        r = await call
    except httpx.HTTPError:
        return Sample(api, (time.perf_counter() - started) * 1000, 0)
    ms = (time.perf_counter() - started) * 1000
    out = None
    if outcome is not None and r.status_code == 200:
        out = outcome(r.json())
    return Sample(api, ms, r.status_code, out)


def general_ops(inp: Inputs) -> list[Op]:
    def near(c: httpx.AsyncClient, rnd: random.Random) -> Awaitable[Sample]:
        lat, lon = rnd.choice(POINTS)
        body = {
            "base": {"lat": lat + rnd.uniform(-0.01, 0.01), "lon": lon + rnd.uniform(-0.01, 0.01)},
            "radius_m": rnd.choice([500, 1000, 3000]),
        }
        return timed("API-005 근접 검색", c.post("/api/locations/search", json=body))

    def filters(c: httpx.AsyncClient, rnd: random.Random) -> Awaitable[Sample]:
        cond = {
            "districts": [rnd.choice(DISTRICTS)],
            "result_type": rnd.choice(["location", "work"]),
        }
        return timed("API-014 필터 검색", c.post("/api/search", json={"conditions": cond}))

    def work(c: httpx.AsyncClient, rnd: random.Random) -> Awaitable[Sample]:
        return timed("API-002 작품 상세", c.get(f"/api/works/{rnd.choice(inp.work_ids)}"))

    def course(c: httpx.AsyncClient, rnd: random.Random) -> Awaitable[Sample]:
        return timed("API-010 추천 코스 상세", c.get(f"/api/courses/{rnd.choice(inp.course_ids)}"))

    def plan(c: httpx.AsyncClient, rnd: random.Random) -> Awaitable[Sample]:
        ids = rnd.sample(inp.location_ids, k=min(len(inp.location_ids), rnd.randint(3, 6)))
        body = {"start": {"type": "location", "id": str(ids[0])}, "location_ids": ids}
        return timed("API-011 코스 경로 계산", c.post("/api/courses/plan", json=body))

    ops: list[Op] = [near, filters, work, plan]
    if inp.course_ids:
        ops.append(course)
    return ops


def _jsonl(name: str) -> list[dict[str, Any]]:
    path = EVAL_DIR / name
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def ai_jobs(
    inp: Inputs, per_api: int, rnd: random.Random
) -> list[Callable[[httpx.AsyncClient], Awaitable[Sample]]]:
    """API마다 per_api개의 서로 다른 요청(캐시 미적중)."""
    jobs: list[Callable[[httpx.AsyncClient], Awaitable[Sample]]] = []
    pairs = [(w, s) for w in inp.intro_work_ids for s in ("none", "partial")]
    rnd.shuffle(pairs)
    for w, s in pairs[:per_api]:
        jobs.append(
            lambda c, w=w, s=s: timed(
                "API-004 AI 작품 소개",
                c.post(f"/api/works/{w}/intro", json={"spoiler": s}, headers=NOTICE),
                lambda b: b.get("status"),
            )
        )
    queries = [q["q"] for q in _jsonl("f3_queries.jsonl") if not q["gold"]["out_of_scope"]]
    rnd.shuffle(queries)
    for q in queries[:per_api]:
        jobs.append(
            lambda c, q=q: timed(
                "API-013 AI 대화형 탐색",
                c.post("/api/query", json={"text": q}, headers=NOTICE),
                lambda b: b.get("status"),
            )
        )
    texts = [t["text"] for t in _jsonl("f4_facility.jsonl")]
    rnd.shuffle(texts)
    if inp.course_stops:
        for i, t in enumerate(texts[:per_api]):
            stops = inp.course_stops[i % len(inp.course_stops)]
            jobs.append(
                lambda c, t=t, stops=stops: timed(
                    "API-012 주변 시설 추천",
                    c.post(
                        "/api/courses/amenities",
                        json={"location_ids": stops, "text": t},
                        headers=NOTICE,
                    ),
                    lambda b: (b.get("conditions") or {}).get("status"),
                )
            )
    rnd.shuffle(jobs)
    return jobs


# ── 실행 ─────────────────────────────────────────────────
async def run_general(
    client: httpx.AsyncClient, inp: Inputs, users: int, duration_s: float, seed: int
) -> list[Sample]:
    rec = Recorder()
    ops = general_ops(inp)
    stop_at = time.monotonic() + duration_s

    async def user(n: int) -> None:
        rnd = random.Random(seed + n)
        while time.monotonic() < stop_at:
            rec.add(await rnd.choice(ops)(client, rnd))

    await asyncio.gather(*(user(n) for n in range(users)))
    return rec.samples


async def run_ai(
    client: httpx.AsyncClient, inp: Inputs, users: int, per_api: int, seed: int
) -> list[Sample]:
    queue: asyncio.Queue = asyncio.Queue()
    for job in ai_jobs(inp, per_api, random.Random(seed)):
        queue.put_nowait(job)
    rec = Recorder()

    async def user() -> None:
        while not queue.empty():
            job = queue.get_nowait()
            rec.add(await job(client))

    await asyncio.gather(*(user() for _ in range(users)))
    return rec.samples


async def main_async(args: argparse.Namespace) -> int:
    timeout = httpx.Timeout(60.0)
    async with httpx.AsyncClient(base_url=args.base, timeout=timeout) as client:
        inp = await discover(client)
        started = time.monotonic()
        if args.mode == "general":
            samples = await run_general(client, inp, args.users, args.duration, args.seed)
            summary = summarize(samples, TARGET_GENERAL_MS)
        else:
            samples = await run_ai(client, inp, args.users, args.per_api, args.seed)
            summary = summarize(samples, TARGET_AI_MS)
    summary.update(
        {
            "mode": args.mode,
            "users": args.users,
            "elapsed_s": round(time.monotonic() - started, 1),
            "inputs": {
                "works": len(inp.work_ids),
                "intro_works": len(inp.intro_work_ids),
                "courses": len(inp.course_ids),
                "locations": len(inp.location_ids),
            },
        }
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / f"load_{args.mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"결과: {path}")
    return 0 if summary["pass"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="부하 테스트 (NFR-001·006)")
    parser.add_argument("mode", choices=("general", "ai"))
    parser.add_argument("--base", default="http://127.0.0.1:8080", help="서비스 주소")
    parser.add_argument("--users", type=int, default=4, help="동시 사용자 수(NFR-001·006: 4)")
    parser.add_argument("--duration", type=float, default=300.0, help="general: 실행 시간(초)")
    parser.add_argument("--per-api", type=int, default=20, help="ai: API마다 보낼 요청 수")
    parser.add_argument("--seed", type=int, default=7, help="입력 선택 난수 시드(재현용)")
    parser.add_argument("--out", type=Path, default=None, help="결과 JSON을 둘 폴더")
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
