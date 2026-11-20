"""F3 AI 대화형 탐색 (COMP-06 QueryService, UC-05·06, SD-02, FR-301~312).

파이프라인(UC-05 기본 흐름):
  1 입력 검증(200자) → 2 마스킹(UC-14) → 3 조건 추출(AI, 10초) → 범위 밖 안내(3c)
  → 4 후속 질의면 세션 조건과 합침(FR-308) → 5 사전 대조(FR-303, 후보 여럿이면 고르기)
  → 6 템플릿 검색(FR-304) + 0건이면 조건 완화(FR-305) → 7 결과 20건
  → 8 상위 10건 근거로 요약·대조(FR-307, COMP-09) → 9 세션 저장
강등: 조건 추출 실패·10초 초과 → 필터 패널(FR-311, 이전 조건 미리 채움).
      요약 실패 → 요약 없이 결과만(8a). 결과 0건 → 요약 생성 안 함(근거 게이트).

AI가 만드는 것은 조건 JSON과 요약 문장뿐이다. 검색은 확정된 조건을 템플릿 매개변수로만
넣는다(AI-REQ-05). 사전 대조(EntityResolver)와 검색 템플릿(QuerySearcher)은 지식베이스
(COMP-12) 쪽 구현을 주입받는다. 기준 위치 좌표는 검색에만 쓰고 AI·세션·로그에 넘기지 않는다(BR-10).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from app.data.ai_gateway import AIUnavailable, LLMClient
from app.data.ai_gateway.tasks import QueryConditions
from app.services.query_session import Session, SessionStore, merge_conditions
from app.support import request_context
from app.support.grounding import Evidence, GroundingVerifier, gate
from app.support.privacy import MASK_NOTICE, MaskedText, mask

MAX_QUERY_CHARS = 200  # FR-301
EXTRACT_DEADLINE_S = 10.0  # FR-311
SUMMARY_DEADLINE_S = 8.0  # NFR-006
MAX_RESULTS = 20  # FR-306
SUMMARY_TOP = 10  # FR-307
SUMMARY_MAX_SENTENCES = 3  # FR-307
# FR-305 조건 완화 순서: 이동 제약 → 개봉연도 → 장르 → 구·군
RELAX_ORDER: tuple[str, ...] = ("travel", "years", "genres", "districts")

SCOPE_NOTICE = (
    "부산의 영화·드라마 촬영지와 작품에 대해서만 답할 수 있어요. 예: '해운대에서 찍은 영화'"
)
AI_DOWN_NOTICE = "AI가 지금 답하지 못해 필터로 찾을 수 있게 열어 두었어요"
NOT_FOUND_NOTICE = "조건에 맞는 촬영지를 찾지 못했어요. 필터로 조건을 바꿔 보세요"
NO_LOCATION_NOTICE = "현재 위치를 받지 못해 '내 위치' 조건을 뺐어요"

Status = Literal["ok", "not_found", "choose", "out_of_scope", "ai_unavailable", "rate_limited"]


class QueryInputError(ValueError):
    """빈 질의·200자 초과(FR-301). API는 400으로 돌려준다."""


# ── 지식베이스 쪽이 채우는 값(포트) ────────────────────────
@dataclass(frozen=True)
class Ref:
    """사전 표준 값(작품·인물·역·장소). id는 지식베이스 ID."""

    id: str
    name: str


@dataclass(frozen=True)
class AnchorFilter:
    type: Literal["station", "place", "current"]
    ref: Ref | None = None  # current면 None(좌표는 요청마다 따로 받는다)


@dataclass(frozen=True)
class TravelFilter:
    mode: Literal["walk", "subway"]
    max_min: int | None = None


@dataclass(frozen=True)
class Filters:
    """확정된 검색 조건 = 검색 템플릿 매개변수(FR-304). 필터 패널(FR-310)도 같은 형식."""

    works: tuple[Ref, ...] = ()
    persons: tuple[Ref, ...] = ()
    genres: tuple[str, ...] = ()  # 장르 묶음 12개(app.core.genres)
    districts: tuple[str, ...] = ()
    year_from: int | None = None
    year_to: int | None = None
    anchor: AnchorFilter | None = None
    travel: TravelFilter | None = None
    result_type: Literal["location", "work"] = "location"

    def has(self, key: str) -> bool:
        if key == "years":
            return self.year_from is not None or self.year_to is not None
        return bool(getattr(self, key))

    def without(self, key: str) -> Filters:
        if key == "years":
            return replace(self, year_from=None, year_to=None)
        if key == "anchor":  # 기준 위치를 빼면 이동 제약도 뜻이 없다
            return replace(self, anchor=None, travel=None)
        empty: object = () if isinstance(getattr(self, key), tuple) else None
        return replace(self, **{key: empty})


@dataclass(frozen=True)
class Option:
    id: str
    label: str  # 예: '친구 (2001, 극영화)'


@dataclass(frozen=True)
class Choice:
    """같은 이름의 후보가 여럿(FR-303 4a). key는 Filters 항목(works·persons·anchor)."""

    key: Literal["works", "persons", "anchor"]
    name: str
    options: tuple[Option, ...]


@dataclass(frozen=True)
class Resolution:
    filters: Filters
    choices: tuple[Choice, ...] = ()  # 고르기 전까지 검색하지 않는다
    unknown: tuple[str, ...] = ()  # 사전에 없어 뺀 이름(FR-303 4b)


class EntityResolver(Protocol):
    """사전 대조(FR-303, COMP-12 dict_entry·임베딩). AI 조건을 표준 값으로 바꾼다."""

    async def resolve(self, conditions: QueryConditions) -> Resolution: ...

    async def pick(self, choice: Choice, option_id: str) -> Ref: ...


@dataclass(frozen=True)
class WorkBrief:
    title: str
    year: int | None = None


@dataclass(frozen=True)
class QueryHit:
    location_id: int
    name: str
    gu: str | None
    lat: float
    lon: float
    works: tuple[WorkBrief, ...] = ()  # 조건에 맞는 작품 먼저
    station: str | None = None  # 가장 가까운 역
    walk_min: int | None = None  # 그 역에서 도보 분(시스템 계산값)
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchPage:
    total: int
    hits: tuple[QueryHit, ...]


class QuerySearcher(Protocol):
    """검색 템플릿 T1~T4(FR-304, COMP-12). 값은 바인딩 매개변수로만 넣는다."""

    async def search(
        self, filters: Filters, *, base_point: tuple[float, float] | None, limit: int
    ) -> SearchPage: ...


# ── 결과 ──────────────────────────────────────────────────
@dataclass(frozen=True)
class Chip:
    """해석 조건 칩(FR-312). key로 지운다."""

    key: str
    label: str


@dataclass(frozen=True)
class QueryResult:
    status: Status
    session_id: str
    notices: tuple[str, ...] = ()
    masked: bool = False  # FR-702 '개인정보로 보이는 내용을 가렸습니다'
    chips: tuple[Chip, ...] = ()
    unknown: tuple[str, ...] = ()
    relaxed: tuple[str, ...] = ()  # 푼 조건(FR-305, 결과 위에 표시)
    total: int = 0
    hits: tuple[QueryHit, ...] = ()
    summary: tuple[str, ...] = ()
    ai_generated: bool = False  # FR-707
    choices: tuple[Choice, ...] = ()
    filter_panel: Filters | None = None  # 필터 패널을 열 때 미리 채울 값(FR-311)
    retry_after_s: int | None = None


@dataclass(frozen=True)
class _Pending:
    """세션에 두는 마지막 상태(칩 지우기·고르기에 다시 쓴다).

    해석된 조건만 둔다. 질문 문장(마스킹본 포함)과 좌표는 넣지 않는다.
    """

    filters: Filters
    choices: tuple[Choice, ...] = ()
    unknown: tuple[str, ...] = ()
    masked: bool = False


RELAX_LABELS = {"travel": "이동 시간", "years": "개봉연도", "genres": "장르", "districts": "구·군"}


def chips(f: Filters) -> tuple[Chip, ...]:
    out: list[Chip] = []
    out += [Chip("works", f"작품: {r.name}") for r in f.works]
    out += [Chip("persons", f"인물: {r.name}") for r in f.persons]
    if f.genres:
        out.append(Chip("genres", f"장르: {', '.join(f.genres)}"))
    if f.districts:
        out.append(Chip("districts", f"구·군: {', '.join(f.districts)}"))
    if f.has("years"):
        a, b = f.year_from, f.year_to
        if a is not None and b is not None:
            label = f"{a}" if a == b else f"{a}~{b}"
        else:
            label = f"{a}~" if a is not None else f"~{b}"
        out.append(Chip("years", f"개봉: {label}"))
    if f.anchor:
        ref = f.anchor.ref
        name = "내 위치" if f.anchor.type == "current" else (ref.name if ref else "")
        out.append(Chip("anchor", f"기준: {name}"))
    if f.travel and f.travel.max_min:
        how = "도보" if f.travel.mode == "walk" else "도시철도"
        out.append(Chip("travel", f"{how} {f.travel.max_min}분 이내"))
    return tuple(out)


def evidence_for(hits: Sequence[QueryHit], total: int, f: Filters) -> Evidence:
    """요약 근거 묶음: 검색 조건 한 줄 + 결과 수 + 상위 10건(시스템 값만, FR-307)."""
    facts: list[str] = []
    names: set[str] = set()
    if c := chips(f):
        facts.append("검색 조건: " + " / ".join(x.label for x in c))
        names.update(r.name for r in (*f.works, *f.persons))
        names.update(f.districts)
        if f.anchor and f.anchor.ref:
            names.add(f.anchor.ref.name)
    facts.append(f"검색 결과: {total}곳")
    for hit in hits[:SUMMARY_TOP]:
        line = hit.name + (f" ({hit.gu})" if hit.gu else "")
        if hit.works:
            line += " — 작품: " + ", ".join(
                w.title + (f"({w.year})" if w.year else "") for w in hit.works[:3]
            )
            names.update(w.title for w in hit.works[:3])
        if hit.station:
            line += f" — {hit.station}" + (f" 도보 {hit.walk_min}분" if hit.walk_min else "")
            names.add(hit.station)
        facts.append(line)
        names.add(hit.name)
        if hit.gu:
            names.add(hit.gu)
    return Evidence(tuple(facts), frozenset(names))


class QueryService:
    def __init__(
        self,
        client: LLMClient,
        resolver: EntityResolver,
        searcher: QuerySearcher,
        *,
        sessions: SessionStore | None = None,
        verifier: GroundingVerifier | None = None,
    ) -> None:
        self._client = client
        self._resolver = resolver
        self._searcher = searcher
        self._sessions = sessions or SessionStore()
        self._verifier = verifier or GroundingVerifier()

    # ── API-013 ───────────────────────────────────────────
    async def ask(
        self,
        text: str,
        *,
        session_id: str | None = None,
        base_point: tuple[float, float] | None = None,
    ) -> QueryResult:
        text = (text or "").strip()
        if not text:
            raise QueryInputError("질문을 입력해 주세요")
        if len(text) > MAX_QUERY_CHARS:
            raise QueryInputError(f"질문은 {MAX_QUERY_CHARS}자까지 쓸 수 있어요")
        session = self._sessions.get(session_id)
        masked = mask(text)
        request_context.note_masked_query(masked)
        notices = (MASK_NOTICE,) if masked.masked else ()
        prev = self._prev_filters(session)

        wait = self._sessions.allow_ai_call(session)
        if wait is not None:
            return QueryResult(
                "rate_limited", session.id, notices, masked.masked, retry_after_s=int(wait)
            )
        try:
            extracted = await self._client.generate(
                "extract_query", {"query": masked}, EXTRACT_DEADLINE_S
            )
        except AIUnavailable as e:
            request_context.note_ai_attempts(e.attempts)
            return QueryResult(
                "ai_unavailable",
                session.id,
                (*notices, AI_DOWN_NOTICE),
                masked.masked,
                filter_panel=prev or Filters(),
            )
        request_context.note_ai_attempts(extracted.attempts)
        conditions: QueryConditions = extracted.data
        if conditions.out_of_scope:  # 3c: 검색하지 않고 세션에도 넣지 않는다
            return QueryResult("out_of_scope", session.id, (*notices, SCOPE_NOTICE), masked.masked)

        merged = merge_conditions(session.last, conditions)
        session.turns.append(merged)
        resolution = await self._resolver.resolve(merged)
        filters, unknown = resolution.filters, resolution.unknown
        if filters.anchor and filters.anchor.type == "current" and base_point is None:
            filters = filters.without("anchor")
            notices = (*notices, NO_LOCATION_NOTICE)
        pending = _Pending(filters, resolution.choices, unknown, masked.masked)
        session.pending = pending
        if resolution.choices:
            return QueryResult(
                "choose",
                session.id,
                notices,
                masked.masked,
                chips=chips(filters),
                unknown=unknown,
                choices=resolution.choices,
            )
        return await self._search_and_summarize(session, pending, notices, base_point, masked)

    # ── 고르기(FR-303 4a) ─────────────────────────────────
    async def choose(
        self,
        session_id: str,
        key: str,
        option_id: str,
        *,
        base_point: tuple[float, float] | None = None,
    ) -> QueryResult:
        session = self._sessions.get(session_id)
        pending: _Pending | None = session.pending
        choice = next((c for c in (pending.choices if pending else ()) if c.key == key), None)
        if pending is None or choice is None:
            raise QueryInputError("고를 항목이 없어요. 질문을 다시 보내 주세요")
        ref = await self._resolver.pick(choice, option_id)
        f = pending.filters
        if key == "anchor":
            f = replace(f, anchor=AnchorFilter(f.anchor.type if f.anchor else "station", ref))
        else:
            f = replace(f, **{key: (*getattr(f, key), ref)})
        left = tuple(c for c in pending.choices if c is not choice)
        pending = replace(pending, filters=f, choices=left)
        session.pending = pending
        if left:
            return QueryResult(
                "choose", session.id, (), pending.masked, chips=chips(f), choices=left
            )
        return await self._search_and_summarize(session, pending, (), base_point)

    # ── 칩 지우기(FR-312, 5a) ──────────────────────────────
    async def remove_chip(
        self,
        session_id: str,
        key: str,
        *,
        base_point: tuple[float, float] | None = None,
    ) -> QueryResult:
        session = self._sessions.get(session_id)
        pending: _Pending | None = session.pending
        if pending is None or pending.choices:
            raise QueryInputError("지울 조건이 없어요. 질문을 다시 보내 주세요")
        if key not in {c.key for c in chips(pending.filters)}:
            raise QueryInputError("없는 조건이에요")
        pending = replace(pending, filters=pending.filters.without(key))
        session.pending = pending
        return await self._search_and_summarize(session, pending, (), base_point)

    # ── API-015 ───────────────────────────────────────────
    def reset(self, session_id: str | None) -> None:
        self._sessions.reset(session_id)

    # ── 6~8단계 ───────────────────────────────────────────
    async def _search_and_summarize(
        self,
        session: Session,
        pending: _Pending,
        notices: tuple[str, ...],
        base_point: tuple[float, float] | None,
        query: MaskedText | None = None,
    ) -> QueryResult:
        filters = pending.filters
        # 고르기·칩 지우기는 질문 문장을 다시 쓰지 않고 해석된 조건을 요약 질문으로 쓴다
        query = query or MaskedText(" / ".join(c.label for c in chips(filters)) or "촬영지")
        page = await self._searcher.search(filters, base_point=base_point, limit=MAX_RESULTS)
        relaxed: list[str] = []
        for key in RELAX_ORDER:  # FR-305
            if page.total > 0:
                break
            if not filters.has(key):
                continue
            filters = filters.without(key)
            relaxed.append(RELAX_LABELS[key])
            page = await self._searcher.search(filters, base_point=base_point, limit=MAX_RESULTS)
        base = dict(
            session_id=session.id,
            masked=pending.masked,
            chips=chips(filters),
            unknown=pending.unknown,
            relaxed=tuple(relaxed),
        )
        if page.total == 0:  # 근거 게이트: 요약 없음, 필터 패널 안내
            return QueryResult(
                "not_found",
                notices=(*notices, NOT_FOUND_NOTICE),
                filter_panel=pending.filters,
                **base,
            )
        hits = page.hits[:MAX_RESULTS]
        summary = await self._summarize(query, hits, page.total, filters)
        return QueryResult(
            "ok",
            notices=notices,
            total=page.total,
            hits=hits,
            summary=summary,
            ai_generated=bool(summary),
            **base,
        )

    async def _summarize(
        self, query: MaskedText, hits: Sequence[QueryHit], total: int, f: Filters
    ) -> tuple[str, ...]:
        evidence = evidence_for(hits, total, f)
        if not hits or not gate(evidence):
            return ()
        try:
            result = await self._client.generate(
                "summarize_results", {"query": query, "evidence": evidence}, SUMMARY_DEADLINE_S
            )
        except AIUnavailable as e:  # 8a: 요약 없이 결과만
            request_context.note_ai_attempts(e.attempts)
            return ()
        request_context.note_ai_attempts(result.attempts)
        checked = self._verifier.verify(
            result.data.sentences, evidence, max_sentences=SUMMARY_MAX_SENTENCES
        )
        return checked.sentences if checked.enough(1) else ()

    @staticmethod
    def _prev_filters(session: Session) -> Filters | None:
        pending: _Pending | None = session.pending
        return pending.filters if pending else None
