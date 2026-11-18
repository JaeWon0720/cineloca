"""F4 AI 대화형 탐색 API: API-013 질의, API-014 필터 패널 검색, API-015 새 대화 (FR-401~412).

- 질의 세션(최근 3턴, 30분)은 서버 메모리에 있어 QueryService는 프로세스에 하나만 둔다.
- 입력 오류(빈 질문·200자 초과·고를 항목 없음)는 400, 세션당 분당 5회 초과는 429(ERR-011).
- AI가 답하지 못하면 200에 status=ai_unavailable과 필터 패널 값을 돌려준다(FR-411).
"""

import logging
from dataclasses import asdict
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.common import ErrorResponse
from app.api.schemas.locations import BasePoint
from app.api.schemas.query import (
    ChooseRequest,
    FiltersModel,
    QueryRequest,
    QueryResponse,
    RemoveChipRequest,
    SearchRequest,
    SearchResponse,
)
from app.data.ai_gateway import (
    AISettings,
    PgAICache,
    PgUsageCounter,
    build_embedder,
    build_llm_client,
)
from app.services.entity_resolver import DictionaryResolver, PgDictionary
from app.services.filter_search import FilterSearchService
from app.services.query_service import (
    AnchorFilter,
    Filters,
    QueryInputError,
    QueryResult,
    QueryService,
    Ref,
    TravelFilter,
)
from app.services.template_searcher import TemplateSearcher

logger = logging.getLogger(__name__)
router = APIRouter(tags=["F4 AI 탐색"])


@lru_cache
def get_query_service() -> QueryService:
    """환경변수(AI_PROVIDER 등)로 AI 공급자를 고른다. 설정이 틀리면 AI 없이(강등) 돈다."""
    try:
        settings = AISettings.from_env()
        client = build_llm_client(settings, usage=PgUsageCounter(), cache=PgAICache())
        embedder = build_embedder(settings)
    except ValueError:
        logger.warning("AI 설정이 올바르지 않아 AI 없이 시작합니다")
        client, embedder = build_llm_client(AISettings(provider="none")), None

    async def embed(text: str) -> list[float]:
        return (await embedder.embed([text]))[0]  # type: ignore[union-attr]

    dictionary = PgDictionary(embed=embed if embedder else None)
    return QueryService(client, DictionaryResolver(dictionary), TemplateSearcher())


@lru_cache
def get_filter_search() -> FilterSearchService:
    return FilterSearchService(TemplateSearcher())


Service = Annotated[QueryService, Depends(get_query_service)]
FilterSearch = Annotated[FilterSearchService, Depends(get_filter_search)]
SessionId = Annotated[str | None, Header(alias="X-Session-Id", max_length=64)]

_BAD = {"model": ErrorResponse, "description": "입력 오류(INVALID_QUERY)"}
_LIMIT = {"model": ErrorResponse, "description": "세션당 분당 5회 초과(RATE_LIMITED, ERR-011)"}
_COMMON = {400: _BAD, 422: ERROR_RESPONSES[422], 503: ERROR_RESPONSES[503]}


def _point(base: BasePoint | None) -> tuple[float, float] | None:
    return (base.lat, base.lon) if base else None


def _bad_request(e: QueryInputError) -> ApiError:
    return ApiError(400, "INVALID_QUERY", str(e))


def _response(result: QueryResult) -> QueryResponse:
    if result.status == "rate_limited":
        raise ApiError(429, "RATE_LIMITED", "잠시 후 다시 시도해 주세요", result.retry_after_s or 1)
    return QueryResponse.model_validate(asdict(result))


def to_filters(m: FiltersModel) -> Filters:
    anchor = None
    if m.anchor:
        ref = Ref(m.anchor.ref.id, m.anchor.ref.name) if m.anchor.ref else None
        anchor = AnchorFilter(m.anchor.type, ref)
    return Filters(
        works=tuple(Ref(r.id, r.name) for r in m.works),
        persons=tuple(Ref(r.id, r.name) for r in m.persons),
        genres=tuple(m.genres),
        districts=tuple(m.districts),
        year_from=m.year_from,
        year_to=m.year_to,
        anchor=anchor,
        travel=TravelFilter(m.travel.mode, m.travel.max_min) if m.travel else None,
        result_type=m.result_type,
    )


@router.post(
    "/query",
    response_model=QueryResponse,
    responses={**_COMMON, 429: _LIMIT},
    summary="API-013 AI 대화형 탐색",
)
async def ask(req: QueryRequest, service: Service, session_id: SessionId = None) -> QueryResponse:
    """질문을 조건으로 바꿔 촬영지를 찾고 요약한다. 후속 질문은 세션 조건에 더한다(FR-408)."""
    try:
        result = await service.ask(req.text, session_id=session_id, base_point=_point(req.base))
    except QueryInputError as e:
        raise _bad_request(e) from None
    return _response(result)


@router.post(
    "/query/choose",
    response_model=QueryResponse,
    responses=_COMMON,
    summary="API-013 후보 고르기",
)
async def choose(
    req: ChooseRequest, service: Service, session_id: SessionId = None
) -> QueryResponse:
    """status=choose 응답의 choices에서 하나를 골라 이어서 검색한다(FR-403)."""
    try:
        result = await service.choose(
            session_id or "", req.key, req.option_id, base_point=_point(req.base)
        )
    except QueryInputError as e:
        raise _bad_request(e) from None
    return _response(result)


@router.post(
    "/query/chips/remove",
    response_model=QueryResponse,
    responses=_COMMON,
    summary="API-013 조건 칩 지우기",
)
async def remove_chip(
    req: RemoveChipRequest, service: Service, session_id: SessionId = None
) -> QueryResponse:
    """해석 조건 하나를 빼고 다시 검색한다(FR-412). AI를 다시 부르지 않는다(요약 제외)."""
    try:
        result = await service.remove_chip(session_id or "", req.key, base_point=_point(req.base))
    except QueryInputError as e:
        raise _bad_request(e) from None
    return _response(result)


@router.delete(
    "/query/session",
    status_code=204,
    responses={422: ERROR_RESPONSES[422]},
    summary="API-015 새 대화(맥락 삭제)",
)
async def reset_session(service: Service, session_id: SessionId = None) -> Response:
    service.reset(session_id)
    return Response(status_code=204)


@router.post(
    "/search",
    response_model=SearchResponse,
    responses=_COMMON,
    summary="API-014 필터 패널 검색",
)
async def filter_search(req: SearchRequest, service: FilterSearch) -> SearchResponse:
    """AI 없이 고른 조건으로 검색한다. 결과가 없으면 조건을 차례로 푼다(FR-405·410)."""
    try:
        result = await service.search(to_filters(req.conditions), base_point=_point(req.base))
    except QueryInputError as e:
        raise _bad_request(e) from None
    return SearchResponse.model_validate(asdict(result))
