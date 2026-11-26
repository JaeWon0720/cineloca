"""방문 인증 업무 로직 (COMP-07 StampService, FR-501~508, BR-06~09, SD-04, ADR-009).

- 서버 지오펜스로 판정한다: 위치 정확도 50m 이하 + 촬영지까지 하버사인 거리 ≤ 인증 반경(BR-07).
- 성공하면 서명 스탬프 토큰을 돌려준다. 서버는 위치·스탬프를 저장하지 않는다(NFR-003).
  위치(GeoFix)는 이 요청 처리 중에만 쓰고 로그·DB·토큰 어디에도 넘기지 않는다.
- '이미 인증'(BR-08)은 브라우저가 보낸 기존 토큰(held)으로만 판단한다(서버에 기록이 없으므로).
- 완주 배지(BR-09): 코스의 인증 대상 장소(BR-06 제외 장소 빼고)를 모두 인증해야 한다.
  인증 대상이 2곳 미만인 코스는 배지를 주지 않는다(1곳짜리 저장 코스로 배지가 바로 나오는 것 방지).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.core.geo import haversine_m
from app.data import courses as course_repo
from app.data import db
from app.data import locations as location_repo
from app.services.map_service import WORKS_PER_HIT, Connect, cert_info
from app.support.tokens import Claims, TokenSigner

KST = timezone(timedelta(hours=9))
MIN_BADGE_TARGETS = 2


# ── 예외: 라우터가 HTTP 오류로 바꾼다 ─────────────────────────
class StampUnavailable(Exception):
    """서명 키가 없거나 잘못됨. 방문 인증만 쓸 수 없다(503)."""


class LocationNotFound(Exception):
    pass


class CourseNotFound(Exception):
    pass


class CertExcluded(Exception):
    """인증 제외 촬영지(BR-06). reason: no_coordinates·private·residential·school·excluded."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── 지오펜스 ────────────────────────────────────────────────
@dataclass(frozen=True)
class GeoFix:
    """요청 처리 중에만 존재하는 사용자 위치. 저장하지 않는다."""

    lat: float
    lon: float
    accuracy_m: float


class GeofenceResult(Enum):
    OK = "ok"
    LOW_ACCURACY = "low_accuracy"
    OUT_OF_RANGE = "out_of_range"


class Geofence:
    MAX_ACCURACY_M = 50  # BR-07

    def check(
        self, fix: GeoFix, lat: float, lon: float, radius_m: int
    ) -> tuple[GeofenceResult, float]:
        distance = haversine_m(fix.lat, fix.lon, lat, lon)
        if fix.accuracy_m > self.MAX_ACCURACY_M:
            return GeofenceResult.LOW_ACCURACY, distance
        if distance > radius_m:
            return GeofenceResult.OUT_OF_RANGE, distance
        return GeofenceResult.OK, distance


# ── 결과 ───────────────────────────────────────────────────
@dataclass(frozen=True)
class StampResult:
    """ok=false면 reason: low_accuracy·out_of_range·already_certified (ERR-008, BR-08)."""

    ok: bool
    token: str | None = None
    reason: str | None = None
    distance_m: int | None = (
        None  # 반경 밖·이미 인증일 때만 알린다(정확도가 나쁘면 거리도 믿을 수 없음)
    )


@dataclass(frozen=True)
class BadgeResult:
    """ok=false면 reason: missing_stamps(missing에 아직 인증 안 한 장소)·too_few_targets."""

    ok: bool
    token: str | None = None
    reason: str | None = None
    course_key: str | None = None
    missing: list[int] = field(default_factory=list)


def saved_course_key(location_ids: Iterable[int]) -> str:
    """브라우저 저장 코스의 지문: 장소 집합이 같으면 같은 배지(순서 무관)."""
    joined = ",".join(str(i) for i in sorted(set(location_ids)))
    return "saved:" + hashlib.sha256(joined.encode()).hexdigest()[:16]


def _kst(iat: int) -> datetime:
    return datetime.fromtimestamp(iat, KST)


class StampService:
    def __init__(
        self,
        signer: TokenSigner | None,
        connect: Connect = db.connection,
        geofence: Geofence | None = None,
    ) -> None:
        self._signer = signer
        self._connect = connect
        self._geofence = geofence or Geofence()

    def _require_signer(self) -> TokenSigner:
        if self._signer is None:
            raise StampUnavailable
        return self._signer

    def _valid_stamps(self, tokens: Iterable[object]) -> dict[int, Claims]:
        signer = self._require_signer()
        return _earliest_stamps(signer.verify(t) for t in tokens)

    async def certify(self, location_id: int, fix: GeoFix, held: Sequence[str]) -> StampResult:
        """API-016. SD-04 순서: 촬영지 확인 → 인증 제외 → 지오펜스 → 이미 인증 → 서명."""
        signer = self._require_signer()
        async with self._connect() as conn:
            target = (await location_repo.cert_targets(conn, [location_id])).get(location_id)
        if target is None:
            raise LocationNotFound
        reason = _excluded_reason(target)
        if reason:
            raise CertExcluded(reason)
        assert target.lat is not None and target.lon is not None
        result, distance = self._geofence.check(fix, target.lat, target.lon, target.cert_radius_m)
        if result is GeofenceResult.LOW_ACCURACY:
            return StampResult(ok=False, reason=result.value)
        if result is GeofenceResult.OUT_OF_RANGE:
            return StampResult(ok=False, reason=result.value, distance_m=round(distance))
        if location_id in self._valid_stamps(held):
            return StampResult(ok=False, reason="already_certified", distance_m=round(distance))
        return StampResult(
            ok=True, token=signer.sign_stamp(location_id), distance_m=round(distance)
        )

    async def verify(self, tokens: Sequence[str]) -> dict[str, Any]:
        """API-017 스탬프북. 유효하지 않은 토큰은 내용 없이 개수만(ERR-010)."""
        signer = self._require_signer()
        claims = [signer.verify(t) for t in tokens]
        stamps = _earliest_stamps(claims)
        badges: dict[str, Claims] = {}
        for c in claims:
            if c and c.typ == "badge" and c.course_key:
                if c.course_key not in badges or c.iat < badges[c.course_key].iat:
                    badges[c.course_key] = c
        course_ids = [int(k.split(":", 1)[1]) for k in badges if _is_recommended(k)]
        async with self._connect() as conn:
            cards = await location_repo.stamp_cards(conn, list(stamps))
            titles = await course_repo.approved_titles(conn, course_ids) if course_ids else {}
        valid = [
            {
                "location": {"id": loc, "name": cards[loc].name},
                "works": [
                    {"id": w.id, "title": w.title, "release_year": w.release_year}
                    for w in cards[loc].works[:WORKS_PER_HIT]
                ],
                "work_count": len(cards[loc].works),
                "certified_at": _kst(c.iat),
            }
            for loc, c in sorted(stamps.items(), key=lambda kv: kv[1].iat)
            if loc in cards
        ]
        badge_items = [
            {
                "course_key": key,
                "course_title": titles.get(int(key.split(":", 1)[1]))
                if _is_recommended(key)
                else None,
                "issued_at": _kst(c.iat),
            }
            for key, c in sorted(badges.items(), key=lambda kv: kv[1].iat)
        ]
        return {
            "valid": valid,
            "badges": badge_items,
            "invalid_count": sum(1 for c in claims if c is None),
            # 서명은 맞지만 데이터 갱신으로 촬영지가 없어진 스탬프(화면에 개수만)
            "unavailable_count": len(stamps) - len(valid),
        }

    async def issue_badge(
        self,
        stamps: Sequence[str],
        *,
        course_id: int | None = None,
        location_ids: Sequence[int] | None = None,
    ) -> BadgeResult:
        """API-018. 추천 코스는 course_id로 DB에서, 저장 코스는 location_ids로 받는다."""
        signer = self._require_signer()
        async with self._connect() as conn:
            if course_id is not None:
                course = await course_repo.approved_stops(conn, course_id)
                if course is None:
                    raise CourseNotFound
                ids, key = course.location_ids, f"course:{course.id}"
            else:
                ids = list(dict.fromkeys(location_ids or []))
                key = saved_course_key(ids)
            targets = await location_repo.cert_targets(conn, ids)
        # 인증 대상: 지금 데이터에 있고 BR-06 제외가 아닌 장소(코스 순서 유지)
        wanted = [i for i in ids if i in targets and not _excluded_reason(targets[i])]
        if len(wanted) < MIN_BADGE_TARGETS:
            return BadgeResult(ok=False, reason="too_few_targets", course_key=key)
        have = self._valid_stamps(stamps)
        missing = [i for i in wanted if i not in have]
        if missing:
            return BadgeResult(ok=False, reason="missing_stamps", course_key=key, missing=missing)
        return BadgeResult(ok=True, token=signer.sign_badge(key), course_key=key)


def _earliest_stamps(claims: Iterable[Claims | None]) -> dict[int, Claims]:
    """유효한 스탬프만, 촬영지마다 가장 먼저 받은 것 하나(BR-08)."""
    found: dict[int, Claims] = {}
    for c in claims:
        if c and c.typ == "stamp" and c.loc is not None:
            if c.loc not in found or c.iat < found[c.loc].iat:
                found[c.loc] = c
    return found


def _excluded_reason(t: location_repo.CertTarget) -> str | None:
    located = t.lat is not None and t.lon is not None
    return cert_info(located, t.caution, t.cert_excluded, t.cert_radius_m, t.name)[
        "excluded_reason"
    ]


def _is_recommended(course_key: str) -> bool:
    kind, _, rest = course_key.partition(":")
    return kind == "course" and rest.isdigit()
