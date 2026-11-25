"""C1 배치 한 번의 단계 조립 단위 테스트 (FR-605·611, #25). DB 없이 이전 반영 값만 바꿔 끼운다."""

import asyncio

import pytest

from app.batch import job, kmdb
from app.batch.geocode import GeoResult
from app.batch.merge import merge
from app.batch.stations import Station
from app.data import staging

ADDR = "부산광역시 중구 광복로 55"


class FakeJuso:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def geocode(self, address: str) -> GeoResult | None:
        self.calls.append(address)
        return GeoResult(35.2, 129.2, "address")


class FakeKakao:
    def __init__(self, found: dict) -> None:
        self.found, self.calls = found, []

    async def geocode(self, name: str, address: str | None) -> GeoResult | None:
        self.calls.append(name)
        if isinstance(self.found.get(name), Exception):
            raise self.found[name]
        return self.found.get(name)


class FakeKmdb:
    has_location = True

    def __init__(self, by_title: dict[str, list[kmdb.Candidate]]) -> None:
        self.by_title = by_title

    async def candidates(self, title: str) -> list[kmdb.Candidate]:
        return self.by_title.get(title, [])


def merged(rows: list[dict]):
    return merge({"DATA-01": rows})


def test_geocode_routes_by_address_and_reuses_only_same_precise_address(monkeypatch) -> None:
    async def prev(conn):
        return {"광복로": (ADDR, 35.1, 129.0), "청사포": (None, 35.16, 129.19)}

    monkeypatch.setattr(staging, "known_coords", prev)
    m = merged(
        [
            {"opusNm": "가", "plcNm": "광복로", "roadNmAddr": ADDR},  # 같은 주소 → 지난 좌표
            {"opusNm": "가", "plcNm": "센텀", "roadNmAddr": "부산 해운대구 센텀5로 55"},
            {"opusNm": "가", "plcNm": "청사포", "roadNmAddr": "부산 해운대구 중동"},  # 동까지만
            {"opusNm": "가", "plcNm": "흰여울"},  # 주소 없음, 못 찾음
            {"opusNm": "가", "plcNm": "오류"},
        ]
    )
    juso = FakeJuso()
    kakao = FakeKakao(
        {"청사포": GeoResult(35.161, 129.191, "keyword"), "오류": RuntimeError("키 abc")}
    )
    c = job.Clients(None, "K", juso, kakao)
    failures: list = []
    out = asyncio.run(job._geocode(c, None, m, failures))

    assert out["광복로"] == (35.1, 129.0, "address") and juso.calls == ["부산 해운대구 센텀5로 55"]
    assert out["센텀"][2] == "address"
    assert out["청사포"] == (35.161, 129.191, "keyword")  # 키워드 좌표는 캐시하지 않음
    assert "흰여울" not in out and "오류" not in out
    assert kakao.calls == ["청사포", "흰여울", "오류"]
    assert [(f.step, f.reason) for f in failures] == [("geocode", "RuntimeError 1건")]


def test_geocode_without_keys_leaves_coords_empty(monkeypatch) -> None:
    async def prev(conn):
        return {}

    monkeypatch.setattr(staging, "known_coords", prev)
    m = merged(
        [{"opusNm": "가", "plcNm": "광복로", "roadNmAddr": ADDR}, {"opusNm": "가", "plcNm": "b"}]
    )
    assert asyncio.run(job._geocode(job.Clients(None, "K"), None, m, [])) == {}


def cand(docid: str, title: str, rel: str = "20090722") -> kmdb.Candidate:
    return kmdb.Candidate(docid=docid, title=title, rel_date=rel, plot="줄거리", link="L")


def test_match_review_decision_wins_and_moves_kmdb_id(monkeypatch) -> None:
    async def decisions(conn):
        return {"해운대": ("rejected", None), "부산행": ("approved", "K9")}

    monkeypatch.setattr(staging, "review_decisions", decisions)
    m = merged(
        [
            {"opusNm": "해운대", "plcNm": "a", "opnDay": "2009-07-22"},
            {"opusNm": "부산행", "plcNm": "a", "opnDay": "2016-07-20"},
            {"opusNm": "부산행 2", "plcNm": "a", "opnDay": "2016-07-20"},
            {"opusNm": "변호인", "plcNm": "a", "opnDay": "2013-12-18"},
        ]
    )
    src = FakeKmdb(
        {
            "해운대": [cand("K1", "해운대")],
            "부산행": [],
            "부산행 2": [cand("K9", "부산행 2", "20160720")],  # 자동이면 K9 → 검수 확정과 겹침
            "변호인": [cand("K3", "변호인", "20131218")],
        }
    )
    rows = asyncio.run(job._match(job.Clients(None, "K", kmdb_source=src), None, m, []))
    by = {r[0]: r for r in rows}

    assert by["해운대"][5] == "unmatched" and by["해운대"][3] is None  # 반려가 자동 연결을 이김
    assert by["부산행"][5:8] == ("matched", by["부산행"][6], "review") and by["부산행"][3] == "K9"
    assert by["부산행"][4] is None  # 후보 본문이 없으면 저장할 KMDb 값도 없음
    assert by["부산행2"][5:8:2] == ("review", "dup") and by["부산행2"][8]["kmdb_id"] == "K9"
    assert by["변호인"][3] == "K3" and by["변호인"][4]["plot"] == "줄거리"


def test_match_without_source_marks_unmatched(monkeypatch) -> None:
    async def decisions(conn):
        return {}

    monkeypatch.setattr(staging, "review_decisions", decisions)
    m = merged([{"opusNm": "해운대", "plcNm": "a"}])
    rows = asyncio.run(job._match(job.Clients(None, "K"), None, m, []))
    assert rows[0][5] == "unmatched" and rows[0][3] is None


def test_locations_carry_source_and_nearest_station() -> None:
    m = merged([{"opusNm": "가", "plcNm": "a", "roadNmAddr": ADDR}, {"opusNm": "가", "plcNm": "b"}])
    sts = [Station("110", "남포", "1호선", 35.0979, 129.0347)]
    locs = {r[0]: r for r in job._locations(m, {"a": (35.0985, 129.0347, "address")}, sts)}
    assert locs["a"][6:8] == ("address", "110") and locs["a"][8] == pytest.approx(1.3, abs=0.2)
    assert locs["b"][4:9] == (None, None, "none", None, None)


def test_unknown_genres_lists_kmdb_genres_outside_groups() -> None:
    # FR-613: 묶음표에 없는 장르는 필터에서 빠지므로 동기화 리포트에 경고로 남긴다
    works = [
        ("a", "가", 2020, "K00001", {"genres": ["드라마", "가짜장르"]}),
        ("b", "나", 2020, None, None),
        ("c", "다", 2020, "K00002", {"genres": ["가짜장르", "새장르"]}),
        ("d", "라", 2020, "K00003", {"plot": "장르 없음"}),
    ]
    assert job.unknown_genres(works) == ["가짜장르", "새장르"]
