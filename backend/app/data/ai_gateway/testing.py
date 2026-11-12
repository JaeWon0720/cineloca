"""테스트용 가짜 공급자. 실제 Ollama·Gemini 없이 업무 서비스·배치를 시험할 때 쓴다(NFR-016)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from app.data.ai_gateway.contracts import Prompt
from app.data.ai_gateway.errors import ProviderError


class FakeProvider:
    """정해 둔 순서대로 응답한다.

    항목: JSON 문자열, dict, Pydantic 모델, 또는 던질 ProviderError.
    """

    def __init__(self, name: str, script: Iterable[Any]) -> None:
        self.name = name
        self._script = list(script)
        self.calls: list[Prompt] = []

    async def complete(self, prompt: Prompt, *, timeout_s: float) -> str:
        self.calls.append(prompt)
        if not self._script:
            raise ProviderError("upstream", "준비한 응답이 없습니다")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            return item.model_dump_json()
        if isinstance(item, dict):
            return json.dumps(item, ensure_ascii=False)
        return str(item)
