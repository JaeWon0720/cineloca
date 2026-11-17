"""근거 대조 검증기 (COMP-09, BR-05, AI-REQ-01·06·07, FR-110·111·311·407).

AI 문장(F4 요약, F1 작품 소개, F3 코스 소개)은 시스템이 만든 근거 묶음만 보고 만들고,
보여 주기 전에 문장마다 다음을 대조해 하나라도 어긋나면 그 문장을 지운다.
  1. 결말·반전 표현(app/core/spoilers.py)        → spoiler
  2. 모델이 적은 인명·작품명·장소명(entities)이 근거 이름에도 근거 줄에도 없음 → entity
  3. 문장에 나온 지식베이스 고유명사(사전)가 근거에 없음     → entity  (사전이 있을 때)
  4. 문장 속 숫자(연도·분·거리·개수)가 근거에 없음          → number
남은 문장이 몇 개면 보여 줄지(F1은 2개 이상, F4는 1개 이상)는 부르는 서비스가 정한다.

    evidence = Evidence(facts=("광안대교 (수영구) — 작품: 친구(2001), 도보 10분",),
                        names=frozenset({"광안대교", "수영구", "친구"}))
    if gate(evidence):                       # 근거가 비면 생성하지 않는다
        ...LLM 호출...
        checked = GroundingVerifier().verify(result.data.sentences, evidence, max_sentences=3)
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.core.spoilers import SPOILER_PHRASES

# 근거에 없어도 쓸 수 있는 이름(서비스 범위 자체)
ALWAYS_ALLOWED: frozenset[str] = frozenset({"부산", "부산광역시"})

# 모델이 entities에 조사까지 붙여 적는 경우("광안대교에서")를 한 번 떼어 본다
_PARTICLES = (
    "에서",
    "으로",
    "에게",
    "까지",
    "부터",
    "에",
    "의",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "과",
    "와",
    "로",
    "도",
)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_STRIP = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    """대조용 정규화: 호환 문자 통일, 소문자, 공백·문장부호 제거."""
    return _STRIP.sub("", unicodedata.normalize("NFKC", text).lower())


def numbers_in(text: str) -> set[str]:
    """'2,001'·'2001년'·'1.5km' → {'2001', '1.5'}. 천 단위 쉼표는 뗀다."""
    return {m.replace(",", "") for m in _NUMBER.findall(unicodedata.normalize("NFKC", text))}


class Claim(Protocol):
    """모델 출력 문장 하나. ai_gateway.tasks.GroundedSentence가 이 모양이다."""

    text: str
    entities: list[str]


class EntityLexicon(Protocol):
    """지식베이스 고유명사 사전(COMP-12 dict_entry). 문장에 나온 이름을 찾는다."""

    def find(self, text: str) -> set[str]: ...


class NameLexicon:
    """이름 목록으로 만든 간단한 사전. 흔한 말과 겹치지 않게 min_len 이상만 찾는다."""

    def __init__(self, names: Iterable[str], *, min_len: int = 3) -> None:
        self._names = {normalize(n): n for n in names if len(normalize(n)) >= min_len}

    def find(self, text: str) -> set[str]:
        flat = normalize(text)
        return {name for key, name in self._names.items() if key in flat}


@dataclass(frozen=True)
class Evidence:
    """근거 묶음. facts는 생성 프롬프트에 그대로 들어가는 줄, names는 대조용 고유명사.

    숫자는 facts·names에 나온 값만 허용한다(시간·거리는 시스템 계산값만 넣는다, SDD 2.3).
    """

    facts: tuple[str, ...]
    names: frozenset[str] = frozenset()

    def allowed_numbers(self) -> set[str]:
        found: set[str] = set()
        for text in (*self.facts, *self.names):
            found |= numbers_in(text)
        return found


def gate(evidence: Evidence | None) -> bool:
    """근거 게이트: 근거가 하나도 없으면 False(생성하지 않는다, AI-REQ-01·FR-407)."""
    return evidence is not None and any(f.strip() for f in evidence.facts)


@dataclass(frozen=True)
class Dropped:
    text: str
    reason: str  # spoiler·entity·number
    detail: str  # 걸린 표현·이름·숫자(로그·평가용)


@dataclass(frozen=True)
class Verified:
    sentences: tuple[str, ...]
    dropped: tuple[Dropped, ...] = field(default=())

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)

    def enough(self, min_sentences: int) -> bool:
        """F1은 2(미만이면 원문 강등, FR-110), F4는 1(0이면 요약 없이 결과만)."""
        return len(self.sentences) >= min_sentences


class GroundingVerifier:
    def __init__(
        self,
        *,
        lexicon: EntityLexicon | None = None,
        spoiler_phrases: Sequence[str] = SPOILER_PHRASES,
        always_allowed: Iterable[str] = ALWAYS_ALLOWED,
    ) -> None:
        self._lexicon = lexicon
        self._spoilers = tuple((normalize(p), p) for p in spoiler_phrases if normalize(p))
        self._always = {normalize(n) for n in always_allowed}

    def verify(
        self,
        claims: Iterable[Claim],
        evidence: Evidence,
        *,
        max_sentences: int | None = None,
    ) -> Verified:
        names = {normalize(n) for n in evidence.names} | self._always
        names.discard("")
        # 이름 목록에 없어도 근거 줄에 그대로 적혀 있으면 근거가 있다(줄거리 속 인물 이름 등)
        names.add(normalize(" ".join(evidence.facts)))
        numbers = evidence.allowed_numbers()
        kept: list[str] = []
        dropped: list[Dropped] = []
        for claim in claims:
            text = claim.text.strip()
            if not text:
                continue
            problem = self._check(text, claim.entities, names, numbers)
            if problem:
                dropped.append(Dropped(text, *problem))
            elif max_sentences is None or len(kept) < max_sentences:
                kept.append(text)
        return Verified(tuple(kept), tuple(dropped))

    def _check(
        self, text: str, entities: Sequence[str], names: set[str], numbers: set[str]
    ) -> tuple[str, str] | None:
        flat = normalize(text)
        for key, phrase in self._spoilers:
            if key in flat:
                return "spoiler", phrase
        mentioned = set(entities) | (self._lexicon.find(text) if self._lexicon else set())
        for entity in sorted(mentioned):
            if not _supported(entity, names):
                return "entity", entity
        for number in sorted(numbers_in(text)):
            if number not in numbers:
                return "number", number
        return None


def _supported(entity: str, names: set[str]) -> bool:
    key = normalize(entity)
    if len(key) < 2:  # 한 글자는 대조 의미가 없다
        return True
    candidates = [key] + [
        key[: -len(p)] for p in _PARTICLES if key.endswith(p) and len(key) - len(p) >= 2
    ]
    # 같은 이름이거나, 근거 이름의 일부(해운대 ⊂ 해운대해수욕장)면 근거가 있다고 본다
    return any(c == n or c in n for c in candidates for n in names)
