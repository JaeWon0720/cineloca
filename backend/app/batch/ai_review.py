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
  match : {"verdict": "match|not_listed|unsure", "candidate": {"docid", "title", "year"} | null,
           "reason": "...", "provider": "...", "prompt_version": "v1"}
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.data.ai_gateway import TASKS, AIUnavailable, LLMClient
from app.data.ai_gateway.tasks import MatchInput, MatchJudgement, TagInput, TagSuggestion

# 한 건의 마감: 공급자 1회 10초(TASKS) × 로컬·Gemini에 스키마 재요청 여유.
# 배치라 사용자가 기다리지 않는다.
ITEM_DEADLINE_S = 25.0


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
        candidate: dict[str, Any] | None = None
        if data.candidate is not None:
            if data.candidate > len(case.candidates):  # 없는 후보 번호는 쓰지 않는다
                stats.invalid += 1
                continue
            chosen = case.candidates[data.candidate - 1]
            candidate = {"docid": chosen.docid, "title": chosen.title, "year": chosen.year}
        stats.providers[result.provider] += 1
        body = {
            "verdict": data.verdict,
            "candidate": candidate,
            "reason": data.reason,
            "provider": result.provider,
            "prompt_version": version,
        }
        proposals.append(ReviewProposal("match", "work", case.work_id, body))
        stats.proposed += 1
    return proposals, stats
