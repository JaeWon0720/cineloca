"""F4 사전 대조 (COMP-06·12 DictionaryResolver, FR-403, UC-05 4·4a·4b).

AI가 뽑은 조건(QueryConditions)의 이름을 지식베이스 표준 값(Ref)으로 바꾼다.
- 작품·인물·역: dict_entry에서 정확 일치 → 트라이그램 0.35 이상 상위 3개(app.data.dictionary).
- 기준 장소(anchor place): 좌표가 있는 촬영지 이름에서 찾는다. 없으면 역 이름으로도 찾아 본다
  ('부산역 근처'를 AI가 장소로 뽑는 경우).
- 후보가 여럿(같은 이름이 여러 개이거나 1등과 점수 차 0.05 안)이면 고르지 않고 Choice로 묻는다.
- 사전에 없는 이름은 조건에서 빼고 unknown으로 알린다. 기준 위치를 못 찾으면 이동 조건도 뺀다.
- 장르·구·군·연도·결과 유형은 AI 출력 스키마가 이미 허용 값만 통과시키므로 그대로 옮긴다.
- 역 이름 없이 '역에서 걸어서 N분'이면 '아무 역'(AnchorFilter(type='station', ref=None))이다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Literal, Protocol

import asyncpg

from app.core.names import station_label
from app.data import db, dictionary
from app.data.ai_gateway.tasks import QueryConditions
from app.data.dictionary import Match, Target, TargetKind
from app.services.query_service import (
    AnchorFilter,
    Choice,
    Filters,
    Option,
    QueryInputError,
    Ref,
    Resolution,
    TravelFilter,
)

logger = logging.getLogger(__name__)

TIE_GAP = 0.05  # 1등과 이 안이면 같은 후보로 보고 사용자에게 묻는다
MAX_OPTIONS = 3

Connect = Callable[[], AbstractAsyncContextManager[asyncpg.Connection]]
Embed = Callable[[str], Awaitable[Sequence[float]]]
ChoiceKey = Literal["works", "persons", "anchor"]


class DictionarySource(Protocol):
    """사전 조회. 테스트는 가짜를 넣는다."""

    async def lookup(self, kind: dictionary.Kind, text: str) -> list[Match]: ...

    async def lookup_place(self, text: str) -> list[Match]: ...

    async def describe(self, kind: TargetKind, ids: Sequence[str]) -> dict[str, Target]: ...


class PgDictionary:
    """app.data.dictionary를 DB 연결에 붙인다. embed가 있으면 트라이그램으로 못 찾은 이름을
    임베딩으로 한 번 더 찾는다(코사인 0.75, embedding이 채워진 행이 있을 때만)."""

    def __init__(self, connect: Connect = db.connection, *, embed: Embed | None = None) -> None:
        self._connect = connect
        self._embed = embed

    async def lookup(self, kind: dictionary.Kind, text: str) -> list[Match]:
        async with self._connect() as conn:
            found = await dictionary.lookup(conn, kind, text)
            if found or self._embed is None or not await dictionary.has_embeddings(conn, kind):
                return found
        try:
            vec = await self._embed(text)
        except Exception:  # 임베딩이 안 되면 보완 없이 '사전에 없음'으로 둔다
            logger.warning("사전 대조 임베딩 실패 kind=%s", kind)
            return []
        async with self._connect() as conn:
            return await dictionary.lookup(conn, kind, text, query_vec=vec)

    async def lookup_place(self, text: str) -> list[Match]:
        async with self._connect() as conn:
            return await dictionary.lookup_place(conn, text)

    async def describe(self, kind: TargetKind, ids: Sequence[str]) -> dict[str, Target]:
        async with self._connect() as conn:
            return await dictionary.describe(conn, kind, ids)


def candidates(matches: Sequence[Match]) -> list[Match]:
    """1등과 점수 차 0.05 안인 후보(표준 값마다 하나). 하나면 확정, 둘 이상이면 고르기."""
    best = max((m.score for m in matches), default=0.0)
    out: dict[str, Match] = {}
    for m in sorted(matches, key=lambda m: -m.score):
        if best - m.score <= TIE_GAP:
            out.setdefault(m.canonical_id, m)
    return list(out.values())[:MAX_OPTIONS]


def display_name(kind: TargetKind, target: Target) -> str:
    return station_label(target.name) if kind == "station" else target.name


def label(kind: TargetKind, target: Target) -> str:
    """고르기 옵션 라벨. 예: '친구 (2001)', '서면역 (1호선)', '영도대교 (영도구)'."""
    name = display_name(kind, target)
    return f"{name} ({target.detail})" if target.detail else name


def _unique(names: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(n.strip() for n in names if n and n.strip()))


class DictionaryResolver:
    def __init__(self, source: DictionarySource | None = None) -> None:
        self._source = source or PgDictionary()

    async def resolve(self, conditions: QueryConditions) -> Resolution:
        c = conditions
        choices: list[Choice] = []
        unknown: list[str] = []

        async def refs(key: ChoiceKey, kind: TargetKind, names: Sequence[str]) -> tuple[Ref, ...]:
            found: dict[str, Ref] = {}
            for name in _unique(names):
                got = await self._one(key, kind, name, await self._source.lookup(kind, name))  # type: ignore[arg-type]
                if isinstance(got, Ref):
                    found.setdefault(got.id, got)
                elif got is None:
                    unknown.append(name)
                else:
                    choices.append(got)
            return tuple(found.values())

        works = await refs("works", "work", c.work_titles)
        persons = await refs("persons", "person", c.persons)

        anchor: AnchorFilter | None = None
        if c.anchor is not None:
            name = (c.anchor.name or "").strip()
            if c.anchor.type == "current":
                anchor = AnchorFilter("current")
            elif c.anchor.type == "station" and not name:
                anchor = AnchorFilter("station")  # 아무 역
            elif name:
                anchor = await self._anchor(c.anchor.type, name, choices, unknown)
        travel = TravelFilter(c.travel.mode, c.travel.max_min) if c.travel and anchor else None

        filters = Filters(
            works=works,
            persons=persons,
            genres=tuple(dict.fromkeys(c.genres)),
            districts=tuple(dict.fromkeys(c.districts)),
            year_from=c.release_year_from,
            year_to=c.release_year_to,
            anchor=anchor,
            travel=travel,
            result_type=c.result_type,
        )
        return Resolution(filters, tuple(choices), tuple(unknown))

    async def pick(self, choice: Choice, option_id: str) -> Ref:
        if option_id not in {o.id for o in choice.options}:
            raise QueryInputError("고를 수 없는 항목이에요")
        kind, _, cid = option_id.partition(":")
        target = (await self._source.describe(kind, [cid])).get(cid)  # type: ignore[arg-type]
        if target is None:  # 고르는 사이 동기화로 없어진 값
            raise QueryInputError("고른 항목을 찾을 수 없어요. 질문을 다시 보내 주세요")
        return Ref(cid, display_name(kind, target))  # type: ignore[arg-type]

    async def _anchor(
        self, type_: str, name: str, choices: list[Choice], unknown: list[str]
    ) -> AnchorFilter | None:
        tries: list[tuple[Literal["station", "place"], TargetKind]] = [("station", "station")]
        if type_ == "place":
            tries.insert(0, ("place", "location"))
        for anchor_type, kind in tries:
            if kind == "location":
                matches = await self._source.lookup_place(name)
            else:
                matches = await self._source.lookup("station", name)
            got = await self._one("anchor", kind, name, matches)
            if isinstance(got, Ref):
                return AnchorFilter(anchor_type, got)
            if got is not None:
                choices.append(got)
                return AnchorFilter(anchor_type)  # 고른 뒤 QueryService.choose가 ref를 채운다
        unknown.append(name)
        return None

    async def _one(
        self, key: ChoiceKey, kind: TargetKind, name: str, matches: Sequence[Match]
    ) -> Ref | Choice | None:
        top = candidates(matches)
        if not top:
            return None
        targets = await self._source.describe(kind, [m.canonical_id for m in top])
        top = [m for m in top if m.canonical_id in targets]
        if not top:
            return None
        if len(top) == 1:
            target = targets[top[0].canonical_id]
            return Ref(target.id, display_name(kind, target))
        options = tuple(
            Option(f"{kind}:{m.canonical_id}", label(kind, targets[m.canonical_id])) for m in top
        )
        return Choice(key, name, options)
