"""AI 응답 캐시 24시간 (AI-REQ-15, ADR-013).

- 키: 작업 이름 + 프롬프트 버전 + 입력(마스킹한 문장, 근거 묶음)의 SHA-256.
  원문은 키에도 남지 않는다.
- 값: 스키마 검증을 통과한 출력(JSON)만.
  사용자 정보·NO_STORE 데이터(카카오 응답, 이미지)는 넣지 않는다.
- 캐시 저장소가 실패하면 캐시 없이 계속한다(조회 실패 = 미적중, 저장 실패 = 무시).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel

from app.data.ai_gateway.contracts import MaskedInput
from app.data.ai_gateway.tasks import TaskSpec

DAY_S = 24 * 60 * 60


class ResponseCache(Protocol):
    """ai_cache 테이블. 구현은 store.PgAICache."""

    async def get(self, key: str) -> dict[str, Any] | None:
        """만료되지 않은 응답. 없으면 None."""
        ...

    async def put(self, key: str, task: str, response: dict[str, Any], ttl_s: int) -> None: ...


def _plain(value: Any) -> Any:
    if isinstance(value, MaskedInput):
        return {"masked": value.text}  # 가린 종류(kinds)는 응답에 영향이 없어 빼고 문장만
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(v) for v in value]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    return value


def cache_key(spec: TaskSpec, inputs: Mapping[str, Any]) -> str:
    """같은 작업·같은 프롬프트 버전·같은 입력이면 같은 키(64자 16진수, ai_cache.key)."""
    payload = json.dumps(
        {"task": spec.name, "version": spec.prompt_version, "inputs": _plain(inputs)},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
