"""건수 가드(FR-612, BR-14)와 동기화 리포트(FR-613) 테스트 (#31). DB 없이 돈다."""

import pytest

from app.batch import guard
from app.batch.report import (
    GUARD_STEP,
    MAX_FAILURES,
    Failure,
    GeoCounts,
    MatchCounts,
    SyncCounts,
    build_report,
    failure,
    limit_failures,
    rates,
)


def counts(works: int = 400, locations: int = 1300) -> SyncCounts:
    return SyncCounts(works=works, locations=locations)


# ── 건수 가드 ──────────────────────────────────────────


def test_guard_passes_normal_sync() -> None:
    result = guard.check(counts(400, 1300), counts(420, 1350))

    assert result.passed and result.reasons == ()


def test_guard_first_sync_checks_minimum_only() -> None:
    assert guard.check(counts(50, 150), None).passed  # 최소 기준과 같으면 통과
    result = guard.check(counts(49, 150), None)
    assert not result.passed
    assert result.reasons == ("작품 수 49편이 최소 기준 50편보다 적습니다",)


def test_guard_rejects_below_minimum_locations() -> None:
    result = guard.check(counts(400, 149), counts(400, 160))

    assert not result.passed
    assert "촬영지 수 149곳이 최소 기준 150곳보다 적습니다" in result.reasons


@pytest.mark.parametrize(
    ("prev", "new", "passed"),
    [
        (100, 71, True),  # 29% 감소
        (100, 70, False),  # 정확히 30% 감소 → '이상'이므로 막음
        (1000, 701, True),  # 29.9%
        (1000, 700, False),
        (300, 210, False),  # 30% (정수 비교로 소수 오차 없음)
        (100, 150, True),  # 증가는 상관없음
    ],
)
def test_guard_drop_boundary(prev: int, new: int, passed: bool) -> None:
    result = guard.check(counts(works=new), counts(works=prev))

    assert result.passed is passed


def test_guard_reports_drop_reason_and_both_counts() -> None:
    result = guard.check(counts(60, 200), counts(120, 1300))

    assert result.reasons == (
        "작품 수가 직전 120편에서 60편으로 50.0% 줄었습니다(30% 이상 감소)",
        "촬영지 수가 직전 1300곳에서 200곳으로 84.6% 줄었습니다(30% 이상 감소)",
    )


def test_guard_ignores_empty_previous_counts() -> None:
    assert guard.check(counts(60, 200), counts(0, 0)).passed


# ── 리포트 ─────────────────────────────────────────────


def test_rates() -> None:
    c = SyncCounts(
        fetched={"DATA-01": 900, "DATA-02": 700},
        works=400,
        locations=1300,
        match=MatchCounts(matched=356, review=4, unmatched=40),
        geo=GeoCounts(located=865, failed=435),
    )

    r = rates(c)

    assert (r.match_rate, r.review_rate, r.geo_rate) == (0.89, 0.01, 0.6654)
    assert rates(SyncCounts()).match_rate is None  # 분모 0이면 null


def test_report_splits_guard_reasons_from_failures() -> None:
    fails = [
        failure(GUARD_STEP, "작품 수 49편이 최소 기준 50편보다 적습니다"),
        failure("fetch", "응답 형식이 바뀜", dataset="DATA-02"),
    ]

    report = build_report(1, "schedule", "guarded", None, None, counts(49), None, fails)

    assert report.guard == ["작품 수 49편이 최소 기준 50편보다 적습니다"]
    assert report.failures == [Failure(dataset="DATA-02", step="fetch", reason="응답 형식이 바뀜")]
    assert report.rates is not None


def test_failure_reason_hides_urls_keys_and_personal_info() -> None:
    exc = RuntimeError(
        "GET https://apis.data.go.kr/x?serviceKey=abc123&pageNo=1 실패, "
        "api_key=SECRET999 문의 010-1234-5678"
    )

    f = failure("fetch", exc, dataset="DATA-01")

    assert f.reason.startswith("RuntimeError: GET [URL 제거]")
    for leaked in ("data.go.kr", "abc123", "SECRET999", "010-1234-5678"):
        assert leaked not in f.reason
    assert "api_key=[제거됨]" in f.reason


def test_failure_reason_is_shortened() -> None:
    assert len(failure("fetch", "가" * 1000).reason) == 300


def test_limit_failures_keeps_count_of_rest() -> None:
    many = [failure("geocode", f"주소 {i}") for i in range(MAX_FAILURES + 50)]

    kept = limit_failures(many)

    assert len(kept) == MAX_FAILURES
    assert kept[-1].reason == "실패 51건을 더 생략했습니다"
    assert limit_failures(many[:3]) == many[:3]


def test_report_warns_unknown_genres() -> None:
    c = SyncCounts(works=400, locations=1300, unknown_genres=["웹툰원작", "AI영화"])

    report = build_report(1, "schedule", "succeeded", None, None, c, None, [])

    assert report.warnings == [
        "장르 묶음표에 없는 KMDb 장르 2종(웹툰원작, AI영화)은 장르 필터에서 빠집니다. "
        "묶음표에 추가해 주세요"
    ]
    assert build_report(1, "schedule", "failed", None, None, None, None, []).warnings == []
