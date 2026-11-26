"""StampService 단위 테스트 (FR-501~508, BR-06~09, AC-11). DB 대신 가짜 저장소를 쓴다."""

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from app.data import courses as course_repo
from app.data import locations as location_repo
from app.data.courses import CourseStops
from app.data.locations import CertTarget, StampCard, WorkRow
from app.services.stamp_service import (
    CertExcluded,
    CourseNotFound,
    Geofence,
    GeofenceResult,
    GeoFix,
    LocationNotFound,
    StampService,
    StampUnavailable,
    haversine_m,
    saved_course_key,
)
from app.support.tokens import TokenSigner

LAT, LON = 35.1, 129.0
M_PER_DEG = math.pi * 6_371_008.8 / 180  # 위도 1도 거리(m)


def north(m: float) -> float:
    return LAT + m / M_PER_DEG


def target(i: int, **kw: object) -> CertTarget:
    base = {"name": f"장소{i}", "lat": LAT, "lon": LON, "caution": None, "cert_radius_m": 100}
    base.update(kw)  # type: ignore[arg-type]
    return CertTarget(id=i, cert_excluded=bool(base.pop("cert_excluded", False)), **base)  # type: ignore[arg-type]


TARGETS = {
    1: target(1),
    2: target(2),
    3: target(3, caution="residential"),
    4: target(4, lat=None, lon=None),
    5: target(5, cert_excluded=True),
    6: target(6, cert_radius_m=300),
    7: target(7, name="[폐업] 삼중다방(중앙동)"),  # 없어진 곳(BR-06)
}


@pytest.fixture(autouse=True)
def fake_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cert_targets(_conn: object, ids: list[int]) -> dict[int, CertTarget]:
        return {i: TARGETS[i] for i in ids if i in TARGETS}

    async def stamp_cards(_conn: object, ids: list[int]) -> dict[int, StampCard]:
        works = [WorkRow(i, f"작품{i}", 2000 + i, True) for i in range(1, 6)]
        return {i: StampCard(i, TARGETS[i].name, works) for i in ids if i in TARGETS}

    async def approved_stops(_conn: object, course_id: int) -> CourseStops | None:
        return CourseStops(9, "부산 바다 코스", [1, 2, 3]) if course_id == 9 else None

    async def approved_titles(_conn: object, ids: list[int]) -> dict[int, str]:
        return {9: "부산 바다 코스"} if 9 in ids else {}

    monkeypatch.setattr(location_repo, "cert_targets", cert_targets)
    monkeypatch.setattr(location_repo, "stamp_cards", stamp_cards)
    monkeypatch.setattr(course_repo, "approved_stops", approved_stops)
    monkeypatch.setattr(course_repo, "approved_titles", approved_titles)


@asynccontextmanager
async def no_db() -> AsyncIterator[None]:
    yield None


SIGNER = TokenSigner("s" * 40, now=lambda: 1_763_000_000)


def service(signer: TokenSigner | None = SIGNER) -> StampService:
    return StampService(signer, connect=no_db)  # type: ignore[arg-type]


def certify(loc: int, meters: float = 0, accuracy: float = 10, held: list[str] | None = None):
    fix = GeoFix(north(meters), LON, accuracy)
    return asyncio.run(service().certify(loc, fix, held or []))


def test_haversine_matches_known_distance() -> None:
    assert abs(haversine_m(LAT, LON, north(100), LON) - 100) < 0.01
    assert abs(haversine_m(0, 0, 0, 1) - 111_195) < 1  # 적도에서 경도 1도
    assert haversine_m(LAT, LON, LAT, LON) == 0


@pytest.mark.parametrize(
    ("meters", "accuracy", "result"),
    [
        (50, 20, GeofenceResult.OK),  # AC-11
        (99.9, 50, GeofenceResult.OK),  # 경계: 정확도 50m는 허용
        (100.1, 10, GeofenceResult.OUT_OF_RANGE),
        (150, 20, GeofenceResult.OUT_OF_RANGE),  # AC-11
        (50, 50.1, GeofenceResult.LOW_ACCURACY),
        (50, 80, GeofenceResult.LOW_ACCURACY),  # AC-11
        (500, 80, GeofenceResult.LOW_ACCURACY),  # 정확도가 나쁘면 거리보다 먼저
    ],
)
def test_geofence_boundaries(meters: float, accuracy: float, result: GeofenceResult) -> None:
    got, _ = Geofence().check(GeoFix(north(meters), LON, accuracy), LAT, LON, 100)

    assert got is result


def test_certify_ac11_flow() -> None:
    ok = certify(1, 50, 20)
    far = certify(1, 150, 20)
    blurry = certify(1, 50, 80)
    again = certify(1, 10, 10, held=[ok.token])  # type: ignore[list-item]

    assert ok.ok and ok.token and ok.distance_m == 50
    assert SIGNER.verify(ok.token).loc == 1  # type: ignore[union-attr]
    assert (far.ok, far.reason, far.distance_m) == (False, "out_of_range", 150)
    assert (blurry.ok, blurry.reason, blurry.distance_m) == (False, "low_accuracy", None)
    assert (again.ok, again.reason, again.token) == (False, "already_certified", None)


def test_held_tokens_for_other_places_or_forged_do_not_block() -> None:
    other = certify(2).token
    forged = TokenSigner("f" * 40).sign_stamp(1)

    assert certify(1, held=[other, forged, "garbage"]).ok  # type: ignore[list-item]


def test_per_location_radius() -> None:
    assert certify(6, 250).ok and not certify(6, 310).ok


@pytest.mark.parametrize(
    ("loc", "reason"), [(3, "residential"), (4, "no_coordinates"), (5, "excluded"), (7, "closed")]
)
def test_excluded_locations_raise(loc: int, reason: str) -> None:
    with pytest.raises(CertExcluded) as e:
        certify(loc)
    assert e.value.reason == reason


def test_unknown_location_and_missing_key() -> None:
    with pytest.raises(LocationNotFound):
        certify(999)
    with pytest.raises(StampUnavailable):
        asyncio.run(service(None).certify(1, GeoFix(LAT, LON, 5), []))


def test_verify_lists_valid_stamps_and_counts_the_rest() -> None:
    later = TokenSigner("s" * 40, now=lambda: 1_763_000_500)
    tokens = [
        later.sign_stamp(1),
        SIGNER.sign_stamp(1),  # 같은 곳 두 장 → 먼저 받은 것 하나
        SIGNER.sign_stamp(2),
        SIGNER.sign_stamp(777),  # 서명은 맞지만 데이터에 없는 촬영지
        SIGNER.sign_badge("course:9"),
        SIGNER.sign_badge(saved_course_key([2, 1])),
        TokenSigner("f" * 40).sign_stamp(2),
        "garbage",
    ]

    out = asyncio.run(service().verify(tokens))

    assert [v["location"]["id"] for v in out["valid"]] == [1, 2]
    assert out["valid"][0]["certified_at"].isoformat() == "2025-11-13T11:13:20+09:00"
    assert len(out["valid"][0]["works"]) == 3 and out["valid"][0]["work_count"] == 5
    assert [b["course_title"] for b in out["badges"]] == ["부산 바다 코스", None]
    assert (out["invalid_count"], out["unavailable_count"]) == (2, 1)


def stamps(*locs: int) -> list[str]:
    return [SIGNER.sign_stamp(i) for i in locs]


def test_badge_for_recommended_course_skips_excluded_stop() -> None:
    s = service()
    partial = asyncio.run(s.issue_badge(stamps(1), course_id=9))
    done = asyncio.run(s.issue_badge(stamps(1, 2), course_id=9))  # 3은 주거지(BR-06) → 대상 아님

    assert (partial.ok, partial.reason, partial.missing) == (False, "missing_stamps", [2])
    assert done.ok and SIGNER.verify(done.token).course_key == "course:9"  # type: ignore[union-attr]
    with pytest.raises(CourseNotFound):
        asyncio.run(s.issue_badge([], course_id=1))


def test_badge_for_saved_course() -> None:
    s = service()
    ok = asyncio.run(s.issue_badge(stamps(2, 1), location_ids=[1, 2, 3, 999]))
    one_target = asyncio.run(s.issue_badge(stamps(1), location_ids=[1, 3, 4]))
    forged = asyncio.run(
        s.issue_badge([TokenSigner("f" * 40).sign_stamp(2), *stamps(1)], location_ids=[1, 2])
    )

    assert ok.ok and ok.course_key == saved_course_key([3, 2, 1, 999])
    assert (one_target.ok, one_target.reason) == (False, "too_few_targets")  # 대상 2곳 미만
    assert (forged.ok, forged.missing) == (False, [2])  # 위조 스탬프는 세지 않음(UC-11)
