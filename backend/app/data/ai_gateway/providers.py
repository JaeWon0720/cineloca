"""실제 AI 공급자 (ADR-003·004, SDD 2.4).

- OllamaProvider: 팀 서버의 로컬 모델(qwen3:8b). 생각 모드를 끄고 JSON 스키마로 출력을 묶는다.
- GeminiProvider: 보조(무료 등급). 키는 URL이 아니라 헤더로 보낸다(로그에 키가 남지 않게).
- OllamaEmbedder: bge-m3 임베딩(1024차원, FR-617).

요청 형식은 착수 PoC p5(prompt v2)에서 확인한 것과 같다.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from app.data.ai_gateway.contracts import Prompt
from app.data.ai_gateway.errors import UPSTREAM, ProviderError
from app.data.ai_gateway.transport import HttpxPost, JsonPost

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic 스키마의 $ref를 $defs 내용으로 펼친다.

    모델 쪽 JSON 문법 변환이 $ref를 못 읽어도 되게 한다.
    """
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                merged = {**walk(copy.deepcopy(defs[ref.removeprefix("#/$defs/")])), **node}
                merged.pop("$ref")
                return merged
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _bad_response(what: str) -> ProviderError:
    return ProviderError(UPSTREAM, f"응답 형식 이상: {what}")


class OllamaProvider:
    def __init__(
        self, base_url: str, model: str, *, post: JsonPost | None = None, name: str = "ollama"
    ) -> None:
        self.name = name
        self._url = base_url.rstrip("/") + "/api/chat"
        self._model = model
        self._post = post or HttpxPost()

    async def complete(self, prompt: Prompt, *, timeout_s: float) -> str:
        body = {
            "model": self._model,
            "stream": False,
            "think": False,  # qwen3 생각 모드 끔(PoC p5: 지연 p95 1.98초)
            "format": inline_refs(prompt.json_schema),
            "options": {"temperature": prompt.temperature},
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }
        data = await self._post(self._url, body, headers={}, timeout_s=timeout_s)
        try:
            content = data["message"]["content"]
        except (KeyError, TypeError):
            raise _bad_response("message.content 없음") from None
        if not isinstance(content, str) or not content.strip():
            raise _bad_response("빈 출력")
        return content


class GeminiProvider:
    def __init__(
        self, api_key: str, model: str, *, post: JsonPost | None = None, name: str = "gemini"
    ) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY가 비어 있습니다")
        self.name = name
        self._url = GEMINI_URL.format(model=model)
        self._key = api_key
        self._post = post or HttpxPost()

    def __repr__(self) -> str:  # 키가 디버그 출력에 찍히지 않게
        return f"GeminiProvider(url={self._url!r})"

    async def complete(self, prompt: Prompt, *, timeout_s: float) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": prompt.system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt.user}]}],
            # 스키마 검증은 체인이 Pydantic으로 한다. PoC p5는 JSON 출력 지정만으로 형식 100%
            "generationConfig": {
                "temperature": prompt.temperature,
                "responseMimeType": "application/json",
            },
        }
        data = await self._post(
            self._url, body, headers={"x-goog-api-key": self._key}, timeout_s=timeout_s
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            raise _bad_response("candidates 없음(차단 또는 빈 응답)") from None
        text = "".join(
            p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")
        )
        if not text.strip():
            raise _bad_response("빈 출력")
        return text


class OllamaEmbedder:
    """bge-m3 임베딩. 로컬 Ollama로만 보낸다(외부 전송 없음)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        dim: int = 1024,
        timeout_s: float = 30.0,
        post: JsonPost | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/api/embed"
        self._model = model
        self._dim = dim
        self._timeout_s = timeout_s
        self._post = post or HttpxPost()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        data = await self._post(
            self._url,
            {"model": self._model, "input": list(texts)},
            headers={},
            timeout_s=self._timeout_s,
        )
        vectors = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise _bad_response("embeddings 개수 불일치")
        if any(not isinstance(v, list) or len(v) != self._dim for v in vectors):
            raise _bad_response(f"벡터 차원이 {self._dim}이 아님")
        return [[float(x) for x in v] for v in vectors]
