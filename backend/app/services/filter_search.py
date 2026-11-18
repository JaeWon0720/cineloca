"""F4 필터 패널 검색 (COMP-06, UC-06, FR-405·406·410, API-014).

AI 없이 사용자가 고른 조건(Filters)으로 바로 검색한다. AI 대화형 탐색(QueryService)과 같은
검색 템플릿(QuerySearcher)·조건 완화 순서(FR-405)를 쓴다. 세션에 넣지 않고 요약도 만들지 않는다.
기준 위치 좌표는 검색에만 쓴다(BR-10).
"""

from __future__ import annotations

from app.services.query_service import (
    MAX_RESULTS,
    NO_LOCATION_NOTICE,
    NOT_FOUND_NOTICE,
    RELAX_LABELS,
    RELAX_ORDER,
    Filters,
    QueryResult,
    QuerySearcher,
    chips,
)


class FilterSearchService:
    def __init__(self, searcher: QuerySearcher) -> None:
        self._searcher = searcher

    async def search(
        self, filters: Filters, *, base_point: tuple[float, float] | None = None
    ) -> QueryResult:
        notices: tuple[str, ...] = ()
        if filters.anchor and filters.anchor.type == "current" and base_point is None:
            filters = filters.without("anchor")
            notices = (NO_LOCATION_NOTICE,)
        asked = filters
        page = await self._searcher.search(filters, base_point=base_point, limit=MAX_RESULTS)
        relaxed: list[str] = []
        for key in RELAX_ORDER:  # FR-405: 이동 시간 → 개봉연도 → 장르 → 구·군
            if page.total > 0:
                break
            if not filters.has(key):
                continue
            filters = filters.without(key)
            relaxed.append(RELAX_LABELS[key])
            page = await self._searcher.search(filters, base_point=base_point, limit=MAX_RESULTS)
        if page.total == 0:
            return QueryResult(
                "not_found",
                "",
                (*notices, NOT_FOUND_NOTICE),
                chips=chips(filters),
                relaxed=tuple(relaxed),
                filter_panel=asked,
            )
        return QueryResult(
            "ok",
            "",
            notices,
            chips=chips(filters),
            relaxed=tuple(relaxed),
            total=page.total,
            hits=page.hits[:MAX_RESULTS],
        )
