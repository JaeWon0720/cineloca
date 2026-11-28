"""장소 사진 단계 테스트 (FR-207, BR-03). 실제 TourAPI 없이 가짜 응답으로 본다."""

import asyncio

import app.batch.__main__ as batch_main
from app.batch.__main__ import cli
from app.batch.photos import Spot, https, match, name_match, refresh
from app.data.tourapi import Place, TourApiClient

BEACH = Spot(1, "해운대해수욕장", 35.1587, 129.1604)


def place(title: str, lat: float = 35.1587, lon: float = 129.1604, *, image: str | None = "https://a/1.jpg",
          type1: bool = True, cid: str = "1") -> Place:  # fmt: skip
    return Place(cid, title, None, lat, lon, None, image=image, kogl_type1=type1)


def test_name_match_exact_contains_and_rejects() -> None:
    assert name_match("해운대해수욕장", "해운대 해수욕장") == "exact"  # 공백 무시
    assert name_match("[폐업] 서라벌 호텔(대청동)", "서라벌호텔") == "exact"  # 머리말·괄호 무시
    assert name_match("달맞이길", "해운대 달맞이길") == "contains"  # 앞에 붙은 말은 허용
    assert (
        name_match("한화리조트(우동)", "한화리조트 해운대") == "contains"
    )  # 뒤에 지역 이름은 허용
    assert name_match("스카이라인 루지", "스카이라인 루지 부산") == "contains"
    # 뒤에 시설 이름이 붙으면 그 안의 시설이라 붙이지 않는다(2026-11-28 실측 오매칭)
    assert name_match("영화의전당", "영화의전당 라이브러리 자료실") is None
    assert (
        name_match("영화의전당 라이브러리", "영화의전당 라이브러리 자료실") is None
    )  # 뒤에 시설명
    # 촬영지가 더 길면 그 안의 다른 장소라 붙이지 않는다(2026-11-28 실측 오매칭)
    assert name_match("새마을금고 청사포지점", "청사포") is None
    assert name_match("해운대해수욕장 삼거리", "해운대해수욕장") is None
    assert name_match("서면", "서면시장") is None  # 촬영지 이름 2글자는 포함으로 인정 안 함
    assert name_match("용두산공원", "자갈치시장") is None


def test_match_distance_limit_and_type1_only() -> None:
    near = place("해운대해수욕장", 35.1587 + 0.0008)  # 약 89m
    far = place("해운대해수욕장", 35.1587 + 0.0010, image="https://a/2.jpg")  # 약 111m
    type3 = place("해운대해수욕장", image="https://a/3.jpg", type1=False)
    no_image = place("해운대해수욕장", image=None)
    got = match([BEACH], [far, type3, no_image, near])
    assert [(p.location_id, p.url, p.distance_m) for p in got] == [(1, "https://a/1.jpg", 89)]
    assert match([BEACH], [far, type3, no_image]) == []


def test_match_one_per_location_exact_first_then_nearest() -> None:
    contains_close = place("부산 해운대해수욕장", image="https://a/c.jpg")  # 0m, 포함
    exact_far = place("해운대 해수욕장", 35.1587 + 0.0005, image="https://a/e.jpg")  # 약 56m, 같음
    got = match([BEACH], [contains_close, exact_far])
    assert [(p.url, p.exact) for p in got] == [("https://a/e.jpg", True)]


def test_https_upgrade() -> None:
    assert https("http://tong.visitkorea.or.kr/a.jpg") == "https://tong.visitkorea.or.kr/a.jpg"
    assert https("https://x/a.jpg") == "https://x/a.jpg"


class FakeConn:
    def __init__(self) -> None:
        self.photo: list[tuple] = []
        self.sources: set[str] = set()

    async def fetch(self, sql: str):
        assert "FROM location WHERE lat IS NOT NULL" in sql
        return [{"id": 1, "name": BEACH.name, "lat": BEACH.lat, "lon": BEACH.lon}]

    def transaction(self):
        conn = self

        class Tx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return Tx()

    async def execute(self, sql: str, *args) -> None:
        if sql.startswith("INSERT INTO source"):
            self.sources.add("DATA-10")
        elif sql.startswith("DELETE FROM photo"):
            self.photo = [r for r in self.photo if r[-1] != args[0]]

    async def executemany(self, sql: str, rows) -> None:
        assert "kogl_type" in sql and ", 1, " in sql  # 제1유형만 넣는다(BR-03)
        self.photo += list(rows)


class FakeGet:
    def __init__(self, items, fail: tuple[int, ...] = ()) -> None:
        self.items = items
        self.fail = fail

    async def __call__(self, url, params, *, timeout_s):
        assert "areaBasedList2" in url
        if params["contentTypeId"] in self.fail:  # 이 종류만 오류 응답
            return {"response": {"header": {"resultCode": "22"}, "body": {}}}
        items = self.items if params["contentTypeId"] == 12 else []
        body = {"totalCount": len(items), "items": {"item": items}}
        return {"response": {"header": {"resultCode": "0000"}, "body": body}}


ITEM = {"contentid": "9", "title": "해운대해수욕장", "mapx": "129.1604", "mapy": "35.1587",
        "firstimage": "http://tong.visitkorea.or.kr/b.jpg", "cpyrhtDivCd": "Type1"}  # fmt: skip


def test_refresh_replaces_without_duplicates() -> None:
    conn = FakeConn()
    client = TourApiClient("K", FakeGet([ITEM, {**ITEM, "contentid": "10"}]))  # 같은 이미지 두 항목
    first = asyncio.run(refresh(conn, client))
    second = asyncio.run(refresh(conn, client))  # 다시 돌려도 건수가 같다
    assert first == second == {"places": 2, "type1_images": 2, "matched": 1, "exact": 1}
    assert conn.photo == [(1, "https://tong.visitkorea.or.kr/b.jpg", "해운대해수욕장", "DATA-10")]
    assert conn.sources == {"DATA-10"}


def test_refresh_keeps_old_photos_when_list_is_empty() -> None:
    conn = FakeConn()
    conn.photo = [(1, "https://old.jpg", "옛 사진", "DATA-10")]
    stats = asyncio.run(refresh(conn, TourApiClient("K", FakeGet([]))))
    assert stats["matched"] == 0 and conn.photo == [(1, "https://old.jpg", "옛 사진", "DATA-10")]


def test_refresh_keeps_old_photos_when_one_type_fails() -> None:
    conn = FakeConn()
    conn.photo = [(1, "https://old.jpg", "옛 사진", "DATA-10")]
    stats = asyncio.run(refresh(conn, TourApiClient("K", FakeGet([ITEM], fail=(39,)))))
    assert stats["places"] == 1 and stats["matched"] == 0 and stats["failed_types"] == [39]
    assert conn.photo == [(1, "https://old.jpg", "옛 사진", "DATA-10")]  # 일부 목록으로 바꾸지 않음


def test_cli_photos_only_needs_database_url_and_key(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    monkeypatch.setattr(batch_main, "ROOT_ENV", tmp_path / "no-root.env")
    assert cli(["--photos-only"]) == 2
    err = capsys.readouterr().err
    assert "DATABASE_URL" in err and "DATA_GO_KR_SERVICE_KEY" in err
