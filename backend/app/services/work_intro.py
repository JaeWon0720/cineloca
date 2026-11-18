"""F1 AI 작품 소개 (COMP-03 일부, FR-108~111, AI-REQ-01·06·07·12, SD-01).

흐름: 대상 확인(KMDb 매칭 작품만) → 스포일러 단계별 근거 묶음(EvidencePolicy) → 근거 게이트
      → LLM(work_intro) → 근거 대조(COMP-09) → 남은 문장 2개 이상이면 소개, 아니면 원문 강등.
- 근거 묶음은 시스템이 지식베이스에서 꺼낸 작품 정보와 부산 촬영지 목록만 쓴다(FR-109).
- '없음' 단계는 줄거리를 근거에 넣지 않는다(AI-REQ-07). 두 단계 모두 결말·반전 표현 검사를 받는다.
- 단계는 플래그가 아니라 정책 객체로 넘긴다(SDD 3.6 제어 결합 제거).
- 강등(AI 장애, 검증 후 2문장 미만): 원문 줄거리와 촬영지 목록을 그대로 보여 준다(FR-110, AC-04).
API(API-004)와 작품 조회(Repository)는 이 서비스를 부르는 쪽에서 붙인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.data.ai_gateway import AIUnavailable, LLMClient
from app.support.grounding import Dropped, Evidence, GroundingVerifier, gate

SpoilerLevel = Literal["none", "partial"]
MIN_SENTENCES = 2  # FR-110: 남은 문장이 2개 미만이면 원문 강등
DEADLINE_S = 8.0  # NFR-006: AI 응답 p95 8초
MAX_CAST = 5  # FR-103: 주요 출연진 최대 5명
MAX_LOCATIONS = 15  # 프롬프트가 지나치게 길어지지 않게(촬영지가 많은 작품)


@dataclass(frozen=True)
class LocationFact:
    name: str
    gu: str | None = None
    scene: str | None = None  # 촬영지 데이터의 장면 설명(있으면)


@dataclass(frozen=True)
class WorkFacts:
    """작품 상세에서 소개에 쓸 값만 모은 것. 작품 Repository가 채운다."""

    work_id: int
    title: str
    kmdb_matched: bool
    release_year: int | None = None
    kind: str | None = None
    genres: tuple[str, ...] = ()
    director: str | None = None
    cast: tuple[str, ...] = ()
    synopsis: str | None = None  # KMDb 원문 줄거리
    locations: tuple[LocationFact, ...] = ()


class EvidencePolicy(Protocol):
    level: SpoilerLevel

    def build(self, work: WorkFacts) -> Evidence: ...


def _base(work: WorkFacts, *, with_scene: bool) -> tuple[list[str], set[str]]:
    facts: list[str] = []
    names: set[str] = {work.title}
    head = f"작품: {work.title}"
    if work.release_year:
        head += f" ({work.release_year})"
    if work.kind:
        head += f", {work.kind}"
    facts.append(head)
    if work.genres:
        facts.append(f"장르: {', '.join(work.genres)}")
    if work.director:
        facts.append(f"감독: {work.director}")
        names.add(work.director)
    cast = work.cast[:MAX_CAST]
    if cast:
        facts.append(f"출연: {', '.join(cast)}")
        names.update(cast)
    for loc in work.locations[:MAX_LOCATIONS]:
        line = f"부산 촬영지: {loc.name}"
        if loc.gu:
            line += f" ({loc.gu})"
            names.add(loc.gu)
        if with_scene and loc.scene:  # 장면 설명은 줄거리를 담을 수 있어 '일부' 단계에만
            line += f" — {loc.scene}"
        facts.append(line)
        names.add(loc.name)
    return facts, names


class NoSpoilerEvidence:
    """'없음' 단계(기본값): 줄거리·장면 설명을 빼고 작품 정보와 촬영지 이름만."""

    level: SpoilerLevel = "none"

    def build(self, work: WorkFacts) -> Evidence:
        facts, names = _base(work, with_scene=False)
        return Evidence(tuple(facts), frozenset(names))


class PartialSpoilerEvidence:
    """'일부' 단계: 줄거리(KMDb 원문)와 촬영지 장면 설명을 더한다.

    결말·반전 문장은 검증기가 지운다.
    """

    level: SpoilerLevel = "partial"

    def build(self, work: WorkFacts) -> Evidence:
        facts, names = _base(work, with_scene=True)
        if work.synopsis and work.synopsis.strip():
            facts.append(f"줄거리: {work.synopsis.strip()}")
        return Evidence(tuple(facts), frozenset(names))


POLICIES: dict[SpoilerLevel, EvidencePolicy] = {
    "none": NoSpoilerEvidence(),
    "partial": PartialSpoilerEvidence(),
}


@dataclass(frozen=True)
class Fallback:
    """강등 화면: 원문 줄거리와 촬영지.

    '없음' 단계에서도 원문은 함께 준다(화면이 접어 두고 사용자가 펼쳐 본다).
    """

    synopsis: str | None
    locations: tuple[LocationFact, ...]


@dataclass(frozen=True)
class IntroResult:
    """status: ok(소개) / degraded(원문 강등) / not_eligible(KMDb 미매칭, FR-108)."""

    status: Literal["ok", "degraded", "not_eligible"]
    level: SpoilerLevel
    sentences: tuple[str, ...] = ()
    fallback: Fallback | None = None
    reason: str | None = None  # 강등 이유: AI_UNAVAILABLE·AI_SCHEMA·AI_DEADLINE·NO_EVIDENCE·TOO_FEW
    provider: str | None = None  # ollama·gemini·cache
    dropped: tuple[Dropped, ...] = ()  # 대조에서 지운 문장(로그·평가용, 화면에는 개수만)
    ai_generated: bool = False  # FR-707 'AI 생성' 표시


class WorkIntroService:
    def __init__(
        self,
        client: LLMClient,
        verifier: GroundingVerifier | None = None,
        *,
        deadline_s: float = DEADLINE_S,
    ) -> None:
        self._client = client
        self._verifier = verifier or GroundingVerifier()
        self._deadline_s = deadline_s

    async def introduce(self, work: WorkFacts, policy: EvidencePolicy) -> IntroResult:
        if not work.kmdb_matched:
            return IntroResult("not_eligible", policy.level)
        fallback = Fallback(work.synopsis, work.locations)
        evidence = policy.build(work)
        # 부산 촬영지 관점 소개라 촬영지가 없으면 근거 부족
        if not gate(evidence) or not work.locations:
            return IntroResult("degraded", policy.level, fallback=fallback, reason="NO_EVIDENCE")
        try:
            result = await self._client.generate(
                "work_intro", {"evidence": evidence}, self._deadline_s
            )
        except AIUnavailable as e:
            return IntroResult("degraded", policy.level, fallback=fallback, reason=e.error_code)
        checked = self._verifier.verify(result.data.sentences, evidence)
        if not checked.enough(MIN_SENTENCES):
            return IntroResult(
                "degraded",
                policy.level,
                fallback=fallback,
                reason="TOO_FEW",
                provider=result.provider,
                dropped=checked.dropped,
            )
        return IntroResult(
            "ok",
            policy.level,
            sentences=checked.sentences,
            provider=result.provider,
            dropped=checked.dropped,
            ai_generated=True,
        )
