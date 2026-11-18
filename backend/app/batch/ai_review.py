"""C1 AI 태그·매칭 제안 (FR-608, BR-04, AI-REQ-04·13).

동기화 배치(COMP-13)가 규칙으로 유형을 정하지 못한 촬영지와 검수 큐의 작품 매칭을 넘기면,
AI Gateway(suggest_tags·judge_match)로 제안을 받아 검수 큐 항목(ReviewProposal)으로 바꾼다.
- 제안은 '미검수'(pending)로 검수 큐에만 들어간다. 촬영지 태그·작품 연결은 바꾸지 않는다(BR-04).
- 한 건씩 차례로 부른다. 로컬 GPU 한 대에 동시에 몰리지 않게 한다(PoC p5 동시 1 기준).
- AI가 실패하면(강등): 태그는 제안 없이 검수 큐에 넣어 사람이 붙이게 하고(SRS 강등 표 'C1'),
  매칭은 규칙이 만든 검수 항목이 이미 있으므로 넣지 않는다. 실패 수는 리포트용으로 센다.

검수 항목 proposal(JSONB) 형식
  tag   : {"tags": [...], "reason": "...", "provider": "ollama", "prompt_version": "v1"}
          실패 시 {"tags": [], "reason": null, "error": "AI_UNAVAILABLE"}
  match : {"verdict": "match|not_listed|unsure", "kmdb_id": "K12345" | null,
           "title": "...", "year": 2001, "reason": "...", "provider": "...", "prompt_version": "v1"}
          kmdb_id는 규칙 항목(#42 staging)과 같은 키다. 승인 처리(FR-609)가 같은 방식으로 읽는다.
          AI 항목은 동기화 반영의 결정을 덮지 않는다(staging.review_decisions는 규칙 항목만 읽음).

run_ai_review()는 동기화 배치가 반영(publish)한 뒤 부르는 한 단계다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.batch import kmdb
from app.data import review_queue
from app.data.ai_gateway import TASKS, AIUnavailable, LLMClient
from app.data.ai_gateway.tasks import (
    MatchCandidate,
    MatchInput,
    MatchJudgement,
    TagInput,
    TagSuggestion,
)

if TYPE_CHECKING:
    import asyncpg

# 한 건의 마감: 공급자 1회 10초(TASKS) × 로컬·Gemini에 스키마 재요청 여유.
# 배치라 사용자가 기다리지 않는다.
ITEM_DEADLINE_S = 25.0
MATCH_CANDIDATES = 5  # AI에 보여 줄 KMDb 후보 수(점수 순)


@dataclass(frozen=True)
class ReviewProposal:
    """review_item 한 행(db/init/02_schema.sql)."""

    kind: Literal["tag", "match"]
    target_type: Literal["location", "work"]
    target_id: int
    proposal: dict[str, Any]
    proposer: Literal["rule", "ai"] = "ai"
    score: float | None = None  # AI 제안은 점수를 매기지 않는다(모델이 말하는 확신도는 믿지 않음)


@dataclass
class SuggestStats:
    """리포트용 건수. failed: AI 강등, invalid: 응답은 왔지만 쓸 수 없음(없는 후보 번호 등)."""

    requested: int = 0
    proposed: int = 0
    failed: int = 0
    invalid: int = 0
    errors: Counter[str] = field(default_factory=Counter)  # AI_UNAVAILABLE·AI_SCHEMA·AI_DEADLINE
    providers: Counter[str] = field(default_factory=Counter)  # 성공한 응답의 공급자


async def suggest_location_tags(
    client: LLMClient,
    places: Iterable[TagInput],
    *,
    item_deadline_s: float = ITEM_DEADLINE_S,
) -> tuple[list[ReviewProposal], SuggestStats]:
    """규칙 미분류 촬영지마다 장소 유형 태그 제안을 만든다(FR-608)."""
    version = TASKS["suggest_tags"].prompt_version
    proposals: list[ReviewProposal] = []
    stats = SuggestStats()
    for place in places:
        stats.requested += 1
        try:
            result = await client.generate("suggest_tags", {"place": place}, item_deadline_s)
        except AIUnavailable as e:
            stats.failed += 1
            stats.errors[e.error_code] += 1
            body: dict[str, Any] = {"tags": [], "reason": None, "error": e.error_code}
        else:
            data: TagSuggestion = result.data
            stats.providers[result.provider] += 1
            body = {
                "tags": list(dict.fromkeys(data.tags)),  # 같은 태그 두 번은 하나로
                "reason": data.reason,
                "provider": result.provider,
                "prompt_version": version,
            }
        proposals.append(ReviewProposal("tag", "location", place.location_id, body))
        stats.proposed += 1
    return proposals, stats


async def judge_matches(
    client: LLMClient,
    cases: Iterable[MatchInput],
    *,
    item_deadline_s: float = ITEM_DEADLINE_S,
) -> tuple[list[ReviewProposal], SuggestStats]:
    """검수 큐에 간 작품 매칭마다 AI 판정 제안을 만든다(FR-608). 후보가 없으면 부르지 않는다."""
    version = TASKS["judge_match"].prompt_version
    proposals: list[ReviewProposal] = []
    stats = SuggestStats()
    for case in cases:
        if not case.candidates:
            continue
        stats.requested += 1
        try:
            result = await client.generate("judge_match", {"case": case}, item_deadline_s)
        except AIUnavailable as e:
            stats.failed += 1
            stats.errors[e.error_code] += 1
            continue
        data: MatchJudgement = result.data
        chosen: MatchCandidate | None = None
        if data.candidate is not None:
            if data.candidate > len(case.candidates):  # 없는 후보 번호는 쓰지 않는다
                stats.invalid += 1
                continue
            chosen = case.candidates[data.candidate - 1]
        stats.providers[result.provider] += 1
        body = {
            "verdict": data.verdict,
            "kmdb_id": chosen.docid if chosen else None,
            "title": chosen.title if chosen else None,
            "year": chosen.year if chosen else None,
            "reason": data.reason,
            "provider": result.provider,
            "prompt_version": version,
        }
        proposals.append(ReviewProposal("match", "work", case.work_id, body))
        stats.proposed += 1
    return proposals, stats


def match_input(
    work_id: int,
    title: str,
    rel_year: int | None,
    candidates: Sequence[kmdb.Candidate],
    *,
    place_keys: set[str],
    places: Sequence[str] = (),
    limit: int = MATCH_CANDIDATES,
) -> MatchInput:
    """KMDb 매칭(#44)이 받은 후보로 judge_match 입력을 만든다.

    규칙과 같은 점수(kmdb.score, 방송사 머리말 뗀 제목)로 상위 limit개를 고르고,
    부산 촬영 근거도 규칙 C와 같은 판단(kmdb._busan_evidence)을 쓴다.
    """
    bare, _media = kmdb.split_media(title)
    cands = list(candidates)
    if rel_year is None:  # decide()와 같이 개봉연도를 모르면 1998년 전 후보는 뺀다
        cands = [
            c
            for c in cands
            if (kmdb.cand_year(c) or kmdb.MIN_YEAR_UNKNOWN) >= kmdb.MIN_YEAR_UNKNOWN
        ]
    ranked = sorted(cands, key=lambda c: -kmdb.score(bare, rel_year, c))[:limit]
    return MatchInput(
        work_id=work_id,
        title=title,
        year=rel_year,
        places=tuple(places),
        candidates=tuple(
            MatchCandidate(
                docid=c.docid,
                title=c.title,
                year=kmdb.cand_year(c),
                kind=c.type or c.use,
                director=c.director,
                busan_evidence=kmdb._busan_evidence(c, place_keys),  # noqa: SLF001 규칙 C와 같은 판단
            )
            for c in ranked
        ),
    )


@dataclass
class AiReviewReport:
    """동기화 리포트에 붙일 AI 제안 건수."""

    tags: SuggestStats
    matches: SuggestStats
    inserted: int  # 검수 큐에 새로 들어간 항목
    stale_removed: int  # 규칙 검수가 사라진 작품의 미검수 AI 매칭 판정(정리)


async def run_ai_review(
    conn: asyncpg.Connection,
    client: LLMClient,
    *,
    match_cases: Iterable[MatchInput] = (),
    tag_limit: int | None = None,
) -> AiReviewReport:
    """반영 뒤 한 번: 태그 없는 촬영지에 태그 제안, 검수 대기 작품에 매칭 판정 제안.

    이미 미검수 AI 제안이 있는 대상은 다시 묻지 않는다(호출 절약). 태그는 규칙(FR-607)이
    붙이지 못해 비어 있는 촬영지가 대상이다. tag_limit으로 한 번에 묻는 수를 줄일 수 있다.
    """
    stale = await review_queue.drop_stale_ai_matches(conn)
    tag_done = await review_queue.pending_targets(conn, "tag")
    match_done = await review_queue.pending_targets(conn, "match")
    places = [
        p for p in await review_queue.untagged_locations(conn) if p.location_id not in tag_done
    ]
    if tag_limit is not None:
        places = places[:tag_limit]
    tag_items, tag_stats = await suggest_location_tags(client, places)
    cases = [c for c in match_cases if c.work_id not in match_done]
    match_items, match_stats = await judge_matches(client, cases)
    inserted = await review_queue.add_proposals(conn, [*tag_items, *match_items])
    return AiReviewReport(tag_stats, match_stats, inserted, stale)
