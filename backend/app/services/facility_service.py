"""F4 코스 주변 시설 추천 (CR-01, FR-415~418).

  1 원하는 조건(선택, 200자) → 마스킹 → AI 조건 구조화(structure_facility, FR-415)
    · 화면에서 고른 분류가 있으면 그걸 따른다. 문장이 없으면 AI를 부르지 않는다.
    · 구조화 실패 → 직접 고르기(FR-418). 고른 값으로 같은 선정 계산을 쓴다.
  2 후보 선정(FR-416): 구간 중간 지점의 음식점·카페, 마지막 촬영지의 숙박(후보는 FR-414가 TourAPI로
    1km 안에서 가져옴)을 조건 일치·거리 가중합으로 점수 → 구간마다 최대 3곳.
    분류는 거르지 않고 점수로만 반영한다(맞는 곳이 늘 먼저, 모자라면 가까운 곳으로 채우고 안내).
    후보에 없는 곳은 추천하지 않는다. AI는 조건만 정하고 장소를 고르지 않는다.
  3 추천 이유(facility_reasons, FR-417): 시설 정보 근거로 한 문장씩 → 근거 대조(COMP-09)
    → 지워지거나 AI가 실패하면 템플릿 문장(FR-418).
조건 문장 원문은 저장하지 않는다(마스킹본만 요청 로그).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.core.facilities import (
    KIND_LABELS,
    PER_SEGMENT,
    SEARCH_RADIUS_M,
    UNVERIFIABLE_PHRASES,
    WEIGHTS,
)
from app.data.ai_gateway import AIUnavailable, LLMClient
from app.data.ai_gateway.tasks import FacilityConditions
from app.support import request_context
from app.support.grounding import Evidence, GroundingVerifier
from app.support.privacy import MASK_NOTICE, mask

MAX_TEXT_CHARS = 200
STRUCTURE_DEADLINE_S = 10.0
REASONS_DEADLINE_S = 8.0
AI_DOWN_NOTICE = "AI가 지금 조건을 정리하지 못해 직접 고를 수 있게 열어 두었어요"
# FR-418, SDD 오류 표: 조회 실패와 0건을 구분해 알린다. 어느 쪽이든 코스는 그대로 쓴다.
FAILED_NOTICE = "시설 정보를 불러오지 못했어요"
EMPTY_NOTICE = "근처 1km 안에 시설이 없어요"
NO_CATEGORY_NOTICE = "고른 분류의 시설이 1km 안에 없어 가까운 곳을 보여 드려요"

Kind = Literal["food", "lodging"]


class FacilityInputError(ValueError):
    """200자 초과·고를 수 없는 분류. API는 400으로 돌려준다."""


# ── 후보(FR-414가 채움) ─────────────────────────────────────
@dataclass(frozen=True)
class FacilityCandidate:
    id: str  # TourAPI contentid
    name: str
    kind: Kind
    category: str | None  # app/core/facilities.py의 분류 이름. 모르면 None(FR-414)
    lat: float
    lon: float
    distance_m: int  # 기준점(구간 중간 지점·마지막 촬영지)에서
    address: str | None = None


@dataclass(frozen=True)
class FacilitySegment:
    """구간 하나의 후보. key 예: '1-2'(1번→2번 구간), 'last'(마지막 촬영지)."""

    key: str
    kind: Kind  # 구간 = food, 마지막 촬영지 = lodging
    anchor: str  # 화면·근거에 쓰는 기준 설명. 예: '자갈치시장~범일동 철길 사이'
    stop_names: tuple[str, ...]  # 기준이 된 촬영지 이름(근거 대조 허용 이름)
    candidates: tuple[FacilityCandidate, ...]
    failed: bool = False  # 후보 검색(FR-414)이 실패한 구간. 0건(빈 candidates)과 구분


# ── 결과 ───────────────────────────────────────────────────
@dataclass(frozen=True)
class FacilityPick:
    facility: FacilityCandidate
    score: float
    parts: dict[str, float]
    matched: tuple[str, ...]  # 맞은 조건(예: '분류: 카페', '키워드: 오션뷰')
    reason: str = ""
    ai_reason: bool = False  # True면 'AI 생성' 표시(FR-707)


@dataclass(frozen=True)
class SegmentPicks:
    key: str
    kind: Kind
    anchor: str
    picks: tuple[FacilityPick, ...]
    notice: str | None = None  # 후보가 없거나 고른 분류가 없을 때 안내


@dataclass(frozen=True)
class ConditionResult:
    """status: ai(문장 구조화) / selected(고른 값만) / manual(구조화 실패, 직접 고르기)."""

    status: Literal["ai", "selected", "manual"]
    conditions: FacilityConditions
    notices: tuple[str, ...] = ()
    masked: bool = False


@dataclass(frozen=True)
class FacilityResult:
    conditions: ConditionResult
    segments: tuple[SegmentPicks, ...]
    notices: tuple[str, ...] = field(default=())


# ── 1 조건 구조화(FR-415) ───────────────────────────────────
def merge_selected(
    ai: FacilityConditions, selected: FacilityConditions | None
) -> FacilityConditions:
    """화면에서 고른 항목이 있으면 그걸 따른다(문장과 다를 때 선택 항목 우선)."""
    if selected is None:
        return ai
    return FacilityConditions(
        kinds=selected.kinds or ai.kinds,
        food=selected.food or ai.food,
        lodging=selected.lodging or ai.lodging,
        keywords=selected.keywords or ai.keywords,
    )


def conditions_from_selection(
    *,
    kinds: Sequence[str] = (),
    food: Sequence[str] = (),
    lodging: Sequence[str] = (),
) -> FacilityConditions:
    """직접 고르기 화면(FR-418)의 값. 목록 밖 값이면 오류."""
    try:
        return FacilityConditions(kinds=list(kinds), food=list(food), lodging=list(lodging))
    except ValueError as e:  # pydantic ValidationError
        raise FacilityInputError("고를 수 없는 분류가 있어요") from e


async def structure_conditions(
    client: LLMClient, text: str | None, selected: FacilityConditions | None = None
) -> ConditionResult:
    text = (text or "").strip()
    if not text:  # 조건 문장은 선택 입력. 없으면 AI를 부르지 않는다
        return ConditionResult("selected", selected or FacilityConditions())
    if len(text) > MAX_TEXT_CHARS:
        raise FacilityInputError(f"조건은 {MAX_TEXT_CHARS}자까지 쓸 수 있어요")
    masked = mask(text)
    request_context.note_masked_query(masked)
    notices = (MASK_NOTICE,) if masked.masked else ()
    try:
        result = await client.generate("structure_facility", {"text": masked}, STRUCTURE_DEADLINE_S)
    except AIUnavailable as e:
        request_context.note_ai_attempts(e.attempts)
        return ConditionResult(
            "manual", selected or FacilityConditions(), (*notices, AI_DOWN_NOTICE), masked.masked
        )
    request_context.note_ai_attempts(result.attempts)
    return ConditionResult("ai", merge_selected(result.data, selected), notices, masked.masked)


# ── 2 후보 선정(FR-416) ─────────────────────────────────────
def _asked(c: FacilityConditions, kind: Kind) -> list[str]:
    return list(c.food if kind == "food" else c.lodging)


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


class FacilityPicker:
    def __init__(
        self,
        weights: dict[str, float] | None = None,
        *,
        per_segment: int = PER_SEGMENT,
        radius_m: int = SEARCH_RADIUS_M,
    ) -> None:
        self._w = dict(WEIGHTS if weights is None else weights)
        if set(self._w) != {"match", "distance"}:
            raise ValueError("가중치 항목은 match·distance")
        self._per = per_segment
        self._radius = radius_m

    def pick(
        self, segments: Iterable[FacilitySegment], cond: FacilityConditions
    ) -> list[SegmentPicks]:
        out: list[SegmentPicks] = []
        used: set[str] = set()  # 같은 시설을 두 구간에 겹쳐 추천하지 않는다
        for seg in segments:
            if cond.kinds and seg.kind not in cond.kinds:
                continue  # 그 종류는 원하지 않음
            if seg.failed:
                out.append(SegmentPicks(seg.key, seg.kind, seg.anchor, (), FAILED_NOTICE))
                continue
            scored = [
                self._score(f, cond, seg.kind)
                for f in seg.candidates
                if f.kind == seg.kind and 0 <= f.distance_m <= self._radius and f.id not in used
            ]
            scored.sort(key=lambda p: (-p.score, p.facility.distance_m, p.facility.id))
            picks = tuple(scored[: self._per])
            used.update(p.facility.id for p in picks)
            asked = _asked(cond, seg.kind)
            notice = None
            if not picks:
                notice = EMPTY_NOTICE
            elif asked and not any(p.facility.category in asked for p in picks):
                notice = NO_CATEGORY_NOTICE
            out.append(SegmentPicks(seg.key, seg.kind, seg.anchor, picks, notice))
        return out

    def _score(self, f: FacilityCandidate, cond: FacilityConditions, kind: Kind) -> FacilityPick:
        asked_categories = _asked(cond, kind)
        facets = 0
        hits = 0
        matched: list[str] = []
        if asked_categories:  # 분류를 골랐으면 맞는지 본다(모르는 분류 None은 안 맞음)
            facets += 1
            if f.category in asked_categories:
                hits += 1
                matched.append(f"분류: {f.category}")
        if cond.keywords:
            facets += 1
            found = [k for k in cond.keywords if _norm(k) and _norm(k) in _norm(f.name)]
            if found:
                hits += 1
                matched.append(f"키워드: {', '.join(found)}")
        parts = {
            "match": hits / facets if facets else 0.0,
            "distance": max(0.0, 1 - f.distance_m / self._radius),
        }
        total = sum(self._w[k] * v for k, v in parts.items())
        return FacilityPick(f, round(total, 6), parts, tuple(matched))


# ── 3 추천 이유(FR-417·418) ─────────────────────────────────
def fact_line(n: int, seg: SegmentPicks, p: FacilityPick) -> str:
    f = p.facility
    label = KIND_LABELS[f.kind] + (f"·{f.category}" if f.category else "")
    line = f"{n}. {f.name} ({label}) — {seg.anchor}에서 {f.distance_m}m"
    if p.matched:
        line += " — 맞은 조건: " + ", ".join(p.matched)
    return line


_NOUN = {"밥": "식당"}  # 템플릿 문장에서 분류 이름 대신 쓸 말
_UNKNOWN_NOUN = {"food": "음식점", "lodging": "숙소"}  # 분류를 모를 때(None)


def _copula(word: str) -> str:
    """받침이 있으면 '이에요', 없으면 '예요'."""
    last = word[-1] if word else ""
    has_final = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return "이에요" if has_final else "예요"


def template_reason(seg: SegmentPicks, p: FacilityPick) -> str:
    """AI 이유가 없을 때(FR-418): 시스템 값만으로 만든 문장."""
    f = p.facility
    noun = _NOUN.get(f.category, f.category) if f.category else _UNKNOWN_NOUN[f.kind]
    extra = f" ({', '.join(p.matched)})" if p.matched else ""
    return f"{seg.anchor}에서 {f.distance_m}m 거리에 있는 {noun}{_copula(noun)}{extra}."


class FacilityService:
    def __init__(
        self,
        client: LLMClient,
        picker: FacilityPicker | None = None,
        verifier: GroundingVerifier | None = None,
    ) -> None:
        self._client = client
        self._picker = picker or FacilityPicker()
        self._verifier = verifier or GroundingVerifier()

    async def recommend(
        self,
        segments: Sequence[FacilitySegment],
        *,
        text: str | None = None,
        selected: FacilityConditions | None = None,
    ) -> FacilityResult:
        cond = await structure_conditions(self._client, text, selected)
        picked = self._picker.pick(segments, cond.conditions)
        stops = {name for seg in segments for name in seg.stop_names}
        with_reasons = await self._reasons(picked, stops)
        return FacilityResult(cond, tuple(with_reasons), cond.notices)

    async def _reasons(self, picked: list[SegmentPicks], stops: set[str]) -> list[SegmentPicks]:
        numbered = [(seg, p) for seg in picked for p in seg.picks]
        if not numbered:
            return picked
        lines = [fact_line(i, seg, p) for i, (seg, p) in enumerate(numbered, start=1)]
        ai: dict[int, str] = {}
        try:
            result = await self._client.generate(
                "facility_reasons", {"evidence": Evidence(tuple(lines))}, REASONS_DEADLINE_S
            )
        except AIUnavailable as e:  # FR-418: 이유만 템플릿으로, 추천은 그대로
            request_context.note_ai_attempts(e.attempts)
        else:
            request_context.note_ai_attempts(result.attempts)
            for r in result.data.reasons:
                if not 1 <= r.number <= len(numbered) or r.number in ai:
                    continue
                seg, p = numbered[r.number - 1]
                if any(w in r.text for w in UNVERIFIABLE_PHRASES):
                    continue  # 후보 데이터에 없는 평가(맛·가격·인기 등)는 지운다
                # 근거 대조는 그 시설 한 줄로만(다른 시설 거리를 섞어 쓰면 지움)
                evidence = Evidence((lines[r.number - 1],), frozenset({p.facility.name, *stops}))
                checked = self._verifier.verify([r], evidence, max_sentences=1)
                if checked.sentences:
                    ai[r.number] = checked.sentences[0]
        out: list[SegmentPicks] = []
        n = 0
        for seg in picked:
            picks = []
            for p in seg.picks:
                n += 1
                text = ai.get(n)
                picks.append(
                    FacilityPick(
                        p.facility,
                        p.score,
                        p.parts,
                        p.matched,
                        reason=text or template_reason(seg, p),
                        ai_reason=text is not None,
                    )
                )
            out.append(SegmentPicks(seg.key, seg.kind, seg.anchor, tuple(picks), seg.notice))
        return out
