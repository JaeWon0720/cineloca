"""F3 AI 맞춤 코스: 취향 구조화와 후보 점수화 (COMP-05 일부, UC-09 1~4, FR-307~309·312).

  1 입력 검증(200자) → 2 마스킹(UC-14) → 3 취향 구조화(AI, structure_preference)
    → 선택 항목(출발지·시간 예산)이 있으면 그걸 따른다(FR-308)
  3a 구조화 실패 → 직접 고르기 화면(FR-312). 고른 값으로 Preference를 만들어 같은 점수화를 쓴다.
  4 후보 점수화(FR-309) → 상위 15곳 → 경로·시간 예산(RoutePlanner·BudgetFitter, 예준)으로 넘긴다.
코스의 장소·순서·시간은 AI가 아니라 계산으로 정한다(UC-09 비고). 취향 문장 원문은 저장하지 않고,
임베딩도 마스킹한 문장으로 로컬 Ollama에서만 만든다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.core.course_scoring import (
    DEFAULT_BUDGET_H,
    MAX_DISTANCE_KM,
    TOP_K,
    WEIGHTS,
    WORKS_CAP,
)
from app.core.genres import GENRE_GROUPS
from app.data.ai_gateway import AIUnavailable, Embedder, LLMClient, ProviderError
from app.data.ai_gateway.tasks import MAX_BUDGET_H, MIN_BUDGET_H, Anchor, Preference
from app.data.course_candidates import CandidateLocation
from app.support import request_context
from app.support.privacy import MASK_NOTICE, MaskedText, mask

MAX_TEXT_CHARS = 200  # FR-307
STRUCTURE_DEADLINE_S = 10.0
AI_DOWN_NOTICE = "AI가 지금 취향을 정리하지 못해 직접 고를 수 있게 열어 두었어요"

_GROUP_OF = {genre: group for group, genres in GENRE_GROUPS.items() for genre in genres}


class PreferenceInputError(ValueError):
    """빈 문장·200자 초과·시간 예산 범위 밖(FR-307). API는 400으로 돌려준다."""


@dataclass(frozen=True)
class PreferenceResult:
    """status: ok(AI 구조화) / manual(직접 고르기 화면, FR-312)."""

    status: Literal["ok", "manual"]
    preference: Preference  # manual이면 선택 항목만 채운 기본값(화면이 미리 채움)
    text: MaskedText | None = None  # 임베딩에 쓸 마스킹 문장(서버에 저장하지 않음)
    notices: tuple[str, ...] = ()
    masked: bool = False
    provider: str | None = None


def _check_budget(budget_h: float | None) -> None:
    if budget_h is not None and not MIN_BUDGET_H <= budget_h <= MAX_BUDGET_H:
        raise PreferenceInputError("시간 예산은 1~8시간이에요")


def apply_selection(
    pref: Preference, *, start: Anchor | None = None, budget_h: float | None = None
) -> Preference:
    """선택 항목이 문장과 다르면 선택 항목을 따른다(FR-308). 예산이 끝내 없으면 4시간."""
    _check_budget(budget_h)
    return pref.model_copy(
        update={
            "start": start or pref.start,
            "time_budget_h": budget_h or pref.time_budget_h or DEFAULT_BUDGET_H,
        }
    )


def preference_from_selection(
    *,
    genres: Sequence[str] = (),
    tags: Sequence[str] = (),
    districts: Sequence[str] = (),
    start: Anchor | None = None,
    budget_h: float | None = None,
) -> Preference:
    """직접 고르기 화면(FR-312)의 값으로 Preference를 만든다. 허용 값 밖이면 오류."""
    _check_budget(budget_h)
    try:
        pref = Preference(genres=list(genres), tags=list(tags), districts=list(districts))
    except ValueError as e:  # pydantic ValidationError
        raise PreferenceInputError("고를 수 없는 값이 있어요") from e
    return apply_selection(pref, start=start, budget_h=budget_h)


class PreferenceService:
    def __init__(self, client: LLMClient) -> None:
        self._client = client

    async def structure(
        self, text: str, *, start: Anchor | None = None, budget_h: float | None = None
    ) -> PreferenceResult:
        text = (text or "").strip()
        if not text:
            raise PreferenceInputError("취향을 입력해 주세요")
        if len(text) > MAX_TEXT_CHARS:
            raise PreferenceInputError(f"취향은 {MAX_TEXT_CHARS}자까지 쓸 수 있어요")
        _check_budget(budget_h)
        masked = mask(text)
        request_context.note_masked_query(masked)
        notices = (MASK_NOTICE,) if masked.masked else ()
        try:
            result = await self._client.generate(
                "structure_preference", {"text": masked}, STRUCTURE_DEADLINE_S
            )
        except AIUnavailable as e:  # 3a: 직접 고르기
            request_context.note_ai_attempts(e.attempts)
            manual = apply_selection(Preference(), start=start, budget_h=budget_h)
            return PreferenceResult(
                "manual", manual, masked, (*notices, AI_DOWN_NOTICE), masked.masked
            )
        request_context.note_ai_attempts(result.attempts)
        pref = apply_selection(result.data, start=start, budget_h=budget_h)
        return PreferenceResult("ok", pref, masked, notices, masked.masked, result.provider)


# ── 후보 점수화(FR-309) ────────────────────────────────────
class CandidateSource(Protocol):
    async def candidates(self) -> list[CandidateLocation]: ...


@dataclass(frozen=True)
class ScoredCandidate:
    location: CandidateLocation
    score: float
    parts: dict[str, float]  # 항목별 점수(0~1, 가중치 곱하기 전) — 평가·디버그용
    matched: tuple[str, ...] = field(default=())  # 맞은 취향(예: '태그: 해변') — 6a 템플릿 사유


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


def _overlap(want: Iterable[str], have: Iterable[str]) -> list[str]:
    have_n = {_norm(h) for h in have}
    return [w for w in want if _norm(w) in have_n]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class CandidateScorer:
    def __init__(self, weights: dict[str, float] | None = None, *, top_k: int = TOP_K) -> None:
        self._w = dict(WEIGHTS if weights is None else weights)
        if set(self._w) != {"match", "similarity", "works", "distance"}:
            raise ValueError("가중치 항목은 match·similarity·works·distance")
        self._top_k = top_k

    def score(
        self,
        candidates: Iterable[CandidateLocation],
        pref: Preference,
        *,
        preference_vector: Sequence[float] | None = None,
        start_point: tuple[float, float] | None = None,
    ) -> list[ScoredCandidate]:
        """피할 유형이 있는 촬영지는 빼고, 점수 높은 순 상위 top_k곳."""
        avoid = set(pref.avoid_tags)
        scored = [
            self._one(c, pref, preference_vector, start_point)
            for c in candidates
            if not avoid.intersection(c.tags)
        ]
        scored.sort(key=lambda s: (-s.score, s.location.location_id))
        return scored[: self._top_k]

    def _one(
        self,
        c: CandidateLocation,
        pref: Preference,
        vec: Sequence[float] | None,
        start: tuple[float, float] | None,
    ) -> ScoredCandidate:
        facets: list[tuple[str, list[str], Iterable[str]]] = [
            ("태그", list(pref.tags), c.tags),
            ("장르", list(pref.genres), {_GROUP_OF[g] for g in c.genres if g in _GROUP_OF}),
            ("작품", pref.work_titles, c.work_titles),
            ("인물", pref.persons, c.persons),
            ("구·군", list(pref.districts), [c.gu] if c.gu else []),
        ]
        asked = [(label, want, have) for label, want, have in facets if want]
        matched: list[str] = []
        hit = 0
        for label, want, have in asked:
            found = _overlap(want, have)
            if found:
                hit += 1
                matched.append(f"{label}: {', '.join(found)}")
        parts = {
            "match": hit / len(asked) if asked else 0.0,
            "similarity": max(0.0, _cosine(vec, c.embedding)) if vec and c.embedding else 0.0,
            "works": min(c.work_count, WORKS_CAP) / WORKS_CAP,
            "distance": (
                max(0.0, 1 - _km(*start, c.lat, c.lon) / MAX_DISTANCE_KM) if start else 0.0
            ),
        }
        total = sum(self._w[k] * v for k, v in parts.items())
        return ScoredCandidate(c, round(total, 6), parts, tuple(matched))


async def preference_vector(
    embedder: Embedder | None, text: MaskedText | None
) -> list[float] | None:
    """취향 문장 임베딩(마스킹 문장, 로컬 Ollama). 없거나 실패하면 None(유사도 0으로 계속)."""
    if embedder is None or text is None:
        return None
    try:
        (vec,) = await embedder.embed([text.text])
    except (ProviderError, ValueError):
        return None
    return vec
