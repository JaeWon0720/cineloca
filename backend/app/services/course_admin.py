"""추천 코스 후보 생성·수정·승인 (API-024, FR-614, FR-401·402·404, BR-12·13).

- 후보 생성: 테마(작품·배우·지역)에 맞는 좌표 있는 촬영지 중 작품이 가장 많이 찍힌 곳을
  기준으로 가까운 곳을 max_stops곳까지 모으고, 이동 시간이 가장 짧은 순서로 늘어놓아
  승인 전(draft)으로 저장한다. 순서는 RoutePlanner(최근접 이웃 + 지역 탐색)가 정하고,
  첫 장소는 모든 장소를 한 번씩 놓아 보고 고른다.
- 수정: 제목·장소·순서·체류 시간·출발역을 고치면 다시 승인 전이 된다
  (승인된 것만 사용자에게 보임).
- 승인: 장소 2~10곳이 모두 좌표가 있어야 한다. 승인된 코스만 API-009·010에 나온다.
시간·거리는 사용자 화면과 같은 계산(CourseService.detail_of)으로 낸다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.core.geo import haversine_m
from app.core.places import DISTRICT_CENTERS
from app.data import courses as repo
from app.data import db
from app.data import locations as location_repo
from app.data import stations as station_repo
from app.services.course_service import (
    MAX_STOPS,
    MIN_STOPS,
    STAY_MIN,
    CourseService,
    LocationNotLocated,
)
from app.services.map_service import Connect
from app.services.route_planner import Point, RoutePlanner, TravelTimeEstimator

APPROVED_BY = "admin"  # 관리자 계정은 하나다(FR-615)
DEFAULT_STOPS = 5
TITLE_MAX = 60
_THEME_TITLE = {
    "work": "「{}」 촬영지 코스",
    "person": "{} 출연작 촬영지 코스",
    "district": "{} 촬영지 코스",
}


class CourseAdminError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _not_found() -> CourseAdminError:
    return CourseAdminError(404, "COURSE_NOT_FOUND", "추천 코스를 찾을 수 없습니다.")


@dataclass(frozen=True)
class StopEdit:
    location_id: int
    stay_min: int = STAY_MIN


def pick_cluster(places: Sequence[repo.ThemePlace], k: int) -> list[repo.ThemePlace]:
    """작품이 가장 많은 곳(places[0])을 기준으로 가까운 곳부터 k곳(기준 포함)."""
    if not places:
        return []
    seed = places[0]
    rest = sorted(places[1:], key=lambda p: (haversine_m(seed.lat, seed.lon, p.lat, p.lon), p.id))
    return [seed, *rest[: k - 1]]


def best_route(points: Sequence[Point], planner: RoutePlanner) -> list[int]:
    """첫 장소를 하나씩 바꿔 보며 이동 시간이 가장 짧은 방문 순서(points 순번)."""
    best: tuple[float, list[int]] | None = None
    for first in range(len(points)):
        order = [first, *[i for i in range(len(points)) if i != first]]
        route = planner.plan(None, [points[i] for i in order])
        visit = [order[i] for i in route.order]
        if best is None or route.travel_min < best[0] - 1e-9:
            best = (route.travel_min, visit)
    assert best is not None
    return best[1]


def check_stops(stops: Sequence[StopEdit]) -> None:
    ids = [s.location_id for s in stops]
    if len(set(ids)) != len(ids):
        raise CourseAdminError(400, "INVALID_REQUEST", "같은 촬영지를 두 번 넣을 수 없습니다.")
    if not MIN_STOPS <= len(ids) <= MAX_STOPS:
        raise CourseAdminError(
            400, "INVALID_REQUEST", f"촬영지는 {MIN_STOPS}~{MAX_STOPS}곳이어야 합니다."
        )
    if any(not 0 <= s.stay_min <= 240 for s in stops):
        raise CourseAdminError(400, "INVALID_REQUEST", "체류 시간은 0~240분이에요.")


def check_title(title: str) -> str:
    title = title.strip()
    if not 1 <= len(title) <= TITLE_MAX:
        raise CourseAdminError(400, "INVALID_REQUEST", f"제목은 1~{TITLE_MAX}자예요.")
    return title


class CourseAdminService:
    def __init__(self, connect: Connect = db.connection, courses: CourseService | None = None):
        self._connect = connect
        self._courses = courses or CourseService(connect)

    # ── 조회 ──
    async def list(self, status: str | None, theme: str | None) -> dict[str, Any]:
        async with self._connect() as conn:
            rows = await repo.all_courses(conn, status, theme)
        items = []
        for row in rows:
            try:
                plan = await self._courses.detail_of(row)
            except LocationNotLocated:
                plan = None
            items.append(
                {
                    **self._head(row),
                    "stop_count": len(row.stops),
                    "total_min": plan["total_min"] if plan else None,
                    "total_distance_m": plan["total_distance_m"] if plan else None,
                    "problem": None if plan else "좌표 없는 촬영지가 있어 계산할 수 없어요",
                }
            )
        return {"items": items}

    async def get(self, course_id: int) -> dict[str, Any]:
        async with self._connect() as conn:
            row = await repo.any_course(conn, course_id)
        if row is None:
            raise _not_found()
        return await self._detail(row)

    @staticmethod
    def _head(row: repo.CourseRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "title": row.title,
            "theme": row.theme,
            "theme_ref": row.theme_ref,
            "status": row.status,
            "approved_at": row.approved_at,
            "start_station_id": row.start_station_id,
        }

    async def _detail(self, row: repo.CourseRow) -> dict[str, Any]:
        try:
            plan = await self._courses.detail_of(row)
        except LocationNotLocated as e:
            raise CourseAdminError(
                409, "LOCATION_NOT_LOCATED", "좌표 없는 촬영지가 있어 계산할 수 없어요."
            ) from e
        return {**plan, **self._head(row)}

    # ── 후보 생성 ──
    async def create_candidate(
        self, theme: str, theme_ref: str, max_stops: int = DEFAULT_STOPS
    ) -> dict[str, Any]:
        if theme == "district":
            if theme_ref not in DISTRICT_CENTERS:
                raise CourseAdminError(400, "INVALID_REQUEST", "부산의 구·군 이름을 골라 주세요.")
        elif not theme_ref.isdigit():
            raise CourseAdminError(400, "INVALID_REQUEST", "작품·인물 번호는 숫자예요.")
        async with self._connect() as conn:
            name = await repo.theme_name(conn, theme, theme_ref)
            places = await repo.theme_places(conn, theme, theme_ref) if name else []
        if name is None:
            raise CourseAdminError(404, "THEME_NOT_FOUND", "작품·인물을 찾을 수 없습니다.")
        chosen = pick_cluster(places, max_stops)
        if len(chosen) < MIN_STOPS:
            raise CourseAdminError(
                409, "NOT_ENOUGH_PLACES", "좌표가 있는 촬영지가 2곳 미만이라 코스를 만들 수 없어요."
            )
        planner = RoutePlanner(TravelTimeEstimator(await self._courses.network()))
        async with self._connect() as conn:
            targets = await location_repo.cert_targets(conn, [p.id for p in chosen])
        points = [
            Point(p.lat, p.lon, targets[p.id].nearest_station_id, targets[p.id].station_walk_min)
            for p in chosen
        ]
        order = best_route(points, planner)
        stops = [StopEdit(chosen[i].id) for i in order]
        title = _THEME_TITLE[theme].format(name)[:TITLE_MAX]
        course_id = await self._save(None, theme, theme_ref, title, None, stops)
        return await self.get(course_id)

    # ── 수정·승인 ──
    async def update(
        self,
        course_id: int,
        *,
        title: str | None = None,
        stops: Sequence[StopEdit] | None = None,
        start_station_id: str | None = None,
        clear_start: bool = False,
    ) -> dict[str, Any]:
        async with self._connect() as conn:
            row = await repo.any_course(conn, course_id)
            if row is None:
                raise _not_found()
            station = start_station_id or (None if clear_start else row.start_station_id)
            if station and await station_repo.get_station(conn, station) is None:
                raise CourseAdminError(404, "STATION_NOT_FOUND", "역을 찾을 수 없습니다.")
        new_stops = list(stops) if stops is not None else [StopEdit(i, m) for i, m in row.stops]
        await self._save(
            course_id,
            row.theme,
            row.theme_ref,
            check_title(title) if title is not None else row.title,
            station,
            new_stops,
        )
        return await self.get(course_id)

    async def _save(
        self,
        course_id: int | None,
        theme: str,
        theme_ref: str | None,
        title: str,
        start_station_id: str | None,
        stops: Sequence[StopEdit],
    ) -> int:
        check_stops(stops)
        ids = [s.location_id for s in stops]
        async with self._connect() as conn:
            targets = await location_repo.cert_targets(conn, ids)
        if missing := [i for i in ids if i not in targets]:
            raise CourseAdminError(
                404, "LOCATION_NOT_FOUND", f"촬영지를 찾을 수 없습니다: {missing}"
            )
        if [i for i in ids if targets[i].lat is None]:
            raise CourseAdminError(
                409, "LOCATION_NOT_LOCATED", "위치가 확인되지 않은 촬영지는 코스에 넣을 수 없어요."
            )
        draft = repo.CourseRow(
            0, theme, theme_ref, title, start_station_id, None,
            [(s.location_id, s.stay_min) for s in stops], status="draft",
        )  # fmt: skip
        plan = await self._courses.detail_of(draft)
        leg_to = {leg["to_order"]: leg for leg in plan["legs"]}
        rows = []
        for n, s in enumerate(stops, start=1):
            leg = leg_to.get(n)
            line = (leg.get("board") or {}).get("line") if leg else None
            rows.append(
                repo.StopIn(
                    s.location_id,
                    s.stay_min,
                    leg["mode"] if leg else None,
                    leg["minutes"] if leg else None,
                    line,
                )
            )
        async with self._connect() as conn, conn.transaction():
            return await repo.save_course(
                conn,
                course_id,
                theme=theme,
                theme_ref=theme_ref,
                title=title,
                start_station_id=start_station_id,
                stops=rows,
                total_min=plan["total_min"],
                total_m=plan["total_distance_m"],
            )

    async def set_status(self, course_id: int, approved: bool) -> dict[str, Any]:
        detail = await self.get(course_id)  # 계산할 수 없는 코스는 여기서 409
        if approved and not MIN_STOPS <= len(detail["stops"]) <= MAX_STOPS:
            raise CourseAdminError(
                409, "INVALID_COURSE", f"촬영지가 {MIN_STOPS}~{MAX_STOPS}곳이어야 승인할 수 있어요."
            )
        async with self._connect() as conn:
            await repo.set_approved(conn, course_id, APPROVED_BY if approved else None)
        return await self.get(course_id)

    async def delete_draft(self, course_id: int) -> None:
        async with self._connect() as conn:
            row = await repo.any_course(conn, course_id)
            if row is None:
                raise _not_found()
            if row.status != "draft" or not await repo.delete_draft(conn, course_id):
                raise CourseAdminError(
                    409,
                    "COURSE_APPROVED",
                    "승인된 코스는 지울 수 없어요. 먼저 승인을 취소해 주세요.",
                )
