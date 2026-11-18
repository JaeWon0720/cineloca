"""C1 동기화 한 번 (FR-601~607·611~613).

수집 → 병합 → 역 → 좌표·최근접 역 → KMDb 매칭 → 스테이징 → 건수 가드 → 반영.

- 단계 실패는 failures에 모은다(주소·키는 남기지 않음). 수집이 통째로 실패하면 'failed'.
- 좌표(#25): 정확한 주소는 주소 API, 주소가 없거나 동까지만 있으면 카카오 키워드 검색.
  지난 반영 좌표는 '같은 정확한 주소'일 때만 다시 쓴다. 키워드 검색 좌표는 매번 다시 찾는다
  (규칙이 바뀌면 바로 반영되고, 리포트의 출처별 건수가 맞도록).
- 매칭: 관리자 검수 결과가 자동 판정보다 우선(FR-611). 같은 KMDb 작품에 두 작품이 붙으면
  하나만 남기고 검수로 돌린다(work.kmdb_id UNIQUE).
"""

import logging
import time
from dataclasses import dataclass, field

import asyncpg
import httpx

from app.batch import collect, kmdb, stations
from app.batch.geocode import JusoGeocoder, KakaoKeywordGeocoder, precise
from app.batch.merge import Merged, merge
from app.batch.report import Failure, GeoCounts, MatchCounts, SyncCounts, failure
from app.batch.sync_record import guard_and_publish, record_failure
from app.data import staging, sync_runs

log = logging.getLogger(__name__)
GEO_SOURCES = ("address", "keyword", "none")


@dataclass
class Clients:
    http: httpx.AsyncClient
    data_go_kr_key: str
    juso: JusoGeocoder | None = None
    kakao: KakaoKeywordGeocoder | None = None
    kmdb_source: kmdb.CandidateSource | None = None
    kmdb_store: bool = True  # DATA-04 STORE(2026-11-11 공공누리 제1유형 확인, #12)


@dataclass
class Stats:
    match_rule: dict = field(default_factory=dict)
    station_located: int = 0
    seconds: float = 0.0


def _count(errors: dict[str, int], e: Exception) -> None:
    errors[type(e).__name__] = errors.get(type(e).__name__, 0) + 1


async def _stations(c: Clients, conn, failures: list[Failure]) -> list[stations.Station]:
    rows = await collect.fetch_stations(c.http, c.data_go_kr_key)
    prev = await staging.known_stations(conn)
    out, errors = [], {}
    for r in rows:
        code, name = str(r.get("scode") or "").strip(), (r.get("sname") or "").strip()
        line = stations.line_of(code)
        if not code or not line:
            continue
        xy = prev.get(code)
        if xy is None and c.juso and r.get("saddr"):
            try:
                g = await c.juso.geocode(r["saddr"])
                xy = (g.lat, g.lon) if g else None
            except Exception as e:  # 한 역이 실패해도 계속
                _count(errors, e)
        if xy:
            out.append(stations.Station(code, name, line, xy[0], xy[1]))
    failures += [failure("station_geocode", f"{k} {n}건", "DATA-08") for k, n in errors.items()]
    return out


async def _geocode(c: Clients, conn, m: Merged, failures: list[Failure]) -> dict[str, tuple]:
    """name_norm → (위도, 경도, 출처 address|keyword)."""
    cache = await staging.known_coords(conn)
    out: dict[str, tuple] = {}
    errors: dict[str, int] = {}
    for key, p in m.places.items():
        try:
            if precise(p.address):
                prev = cache.get(key)
                if prev and prev[0] == p.address:
                    out[key] = (prev[1], prev[2], "address")
                    continue
                g = await c.juso.geocode(p.address) if c.juso else None
            else:
                g = await c.kakao.geocode(p.name, p.address) if c.kakao else None
        except Exception as e:
            _count(errors, e)
            continue
        if g:
            out[key] = (g.lat, g.lon, g.via)
    # 장소별 사유 대신 오류 종류별 건수만 남긴다(주소를 남기지 않음)
    failures += [failure("geocode", f"{k} {n}건", "DATA-06") for k, n in sorted(errors.items())]
    return out


async def _match(c: Clients, conn, m: Merged, failures: list[Failure]) -> list[tuple]:
    decisions = await staging.review_decisions(conn)
    places_of: dict[str, set[str]] = {}
    for wk, pk, _ in m.shootings:
        places_of.setdefault(wk, set()).add(pk)
    results: dict[str, kmdb.MatchResult] = {}
    cands_of: dict[str, list[kmdb.Candidate]] = {}
    errors: dict[str, int] = {}
    for key, w in m.works.items():
        cands: list[kmdb.Candidate] = []
        if c.kmdb_source:
            try:
                cands = await c.kmdb_source.candidates(kmdb.split_media(w.title)[0])
            except Exception as e:
                _count(errors, e)
        cands_of[key] = cands
        results[key] = kmdb.decide(w.title, w.release_year, cands, places_of.get(key, set()))
    failures += [failure("match", f"{k} {n}건", "DATA-04") for k, n in sorted(errors.items())]
    results = kmdb.demote_duplicates(results)

    # 검수 확정이 우선(FR-611). 확정된 KMDb 작품을 자동으로 잡은 다른 작품은 검수로 돌린다
    fixed: dict[str, str] = {}
    for key, (status, kid) in decisions.items():
        r = results.get(key)
        if r is None:
            continue
        if status in ("approved", "modified") and kid:
            best = next((x for x in cands_of[key] if x.docid == kid), None)
            # 관리자가 고른 작품이 이번 후보에 없으면 id만 연결하고 KMDb 본문은 비운다
            best = best or kmdb.Candidate(docid=kid, title="")
            results[key] = kmdb.MatchResult("matched", "review", r.score, best)
            fixed[kid] = key
        elif status == "rejected":
            results[key] = kmdb.MatchResult("unmatched", "review", r.score, None)
    for key, r in results.items():
        if r.status == "matched" and r.best and fixed.get(r.best.docid, key) != key:
            results[key] = kmdb.MatchResult("review", "dup", r.score, r.best)

    rows = []
    for key, w in m.works.items():
        r = results[key]
        best = r.best if r.status != "unmatched" else None
        cand = (
            {
                "kmdb_id": best.docid,
                "kmdb_title": best.title,
                "prod_year": best.prod_year,
                "rel_date": best.rel_date,
            }
            if r.status == "review" and best
            else None
        )
        matched = r.status == "matched" and best is not None
        store = kmdb.stored_kmdb(best) if matched and c.kmdb_store and best.title else None
        rows.append(
            (
                key,
                w.title,
                w.release_year,
                best.docid if matched else None,
                store,
                r.status,
                round(r.score, 3),
                r.rule,
                cand,
                w.source_id,
            )
        )
    return rows


def _locations(m: Merged, coords: dict[str, tuple], sts: list[stations.Station]) -> list[tuple]:
    locs = []
    for key, p in m.places.items():
        lat, lon, via = coords.get(key, (None, None, "none"))
        near = stations.nearest(lat, lon, sts) if lat is not None else None
        locs.append(
            (
                key,
                p.name,
                p.address,
                p.gu,
                lat,
                lon,
                via,
                near[0].id if near else None,
                near[1] if near else None,
            )
        )
    return locs


async def run(
    conn: asyncpg.Connection, c: Clients, trigger: str = "manual"
) -> tuple[int, SyncCounts, Stats]:
    t0 = time.monotonic()
    run_id = await sync_runs.start(conn, trigger)
    failures: list[Failure] = []
    counts = SyncCounts()
    try:
        raw = await collect.fetch_busan(c.http, c.data_go_kr_key)
        counts.fetched = {sid: len(rows) for sid, rows in raw.items()}
        m = merge(raw)
        sts = await _stations(c, conn, failures)
        counts.fetched["DATA-08"] = len(sts)
        coords = await _geocode(c, conn, m, failures)
        works = await _match(c, conn, m, failures)
    except Exception as e:
        await record_failure(conn, run_id, [failure("collect", e), *failures], counts)
        raise
    locs = _locations(m, coords, sts)
    counts.works, counts.locations = len(works), len(locs)
    counts.match = MatchCounts(
        **{s: sum(1 for w in works if w[5] == s) for s in ("matched", "review", "unmatched")}
    )
    by_source = {v: sum(1 for x in locs if x[6] == v) for v in GEO_SOURCES}
    counts.geo = GeoCounts(
        located=len(locs) - by_source["none"], failed=by_source["none"], by_source=by_source
    )
    async with conn.transaction():
        await staging.reset(conn)
        await staging.write_stations(
            conn, [(s.id, s.name, s.line, s.lat, s.lon, s.gu) for s in sts]
        )
        await staging.write_works(conn, works)
        await staging.write_locations(conn, locs)
        await staging.write_shootings(
            conn,
            [(s.work_key, s.place_key, s.source_id, s.raw, None) for s in m.shootings.values()],
        )
    await guard_and_publish(conn, run_id, counts, failures, staging.publish)
    stats = Stats(
        match_rule={
            r: sum(1 for w in works if w[7] == r and w[5] == "matched")
            for r in ("score", "rule_c", "rule_u", "review")
        },
        station_located=len(sts),
        seconds=round(time.monotonic() - t0, 1),
    )
    log.info("동기화 %s 끝: %.1f초", run_id, stats.seconds)
    return run_id, counts, stats
