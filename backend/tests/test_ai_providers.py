"""실제 AI 공급자 테스트 (ADR-003·004, NFR-009). 네트워크 없이 가짜 HTTP로 시험한다."""

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import pytest

from app.data.ai_gateway import (
    TASKS,
    AISettings,
    AIUnavailable,
    DefaultPromptBuilder,
    GeminiProvider,
    OllamaEmbedder,
    OllamaProvider,
    ProviderError,
    build_embedder,
    build_llm_client,
)
from app.data.ai_gateway.providers import inline_refs
from app.data.ai_gateway.transport import error_for_status, retry_after_seconds
from app.support.privacy import mask

KEY = "test-gemini-key-not-real"
CONDITIONS = {
    "out_of_scope": False,
    "work_titles": [],
    "persons": [],
    "genres": ["범죄·느와르"],
    "districts": ["해운대구"],
    "release_year_from": None,
    "release_year_to": None,
    "anchor": None,
    "travel": None,
    "result_type": "location",
}


class FakePost:
    """JsonPost 가짜. 받은 요청을 기록하고 정해진 응답(또는 예외)을 차례로 돌려준다."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, url: str, payload: Mapping[str, Any], *, headers: Mapping[str, str], timeout_s: float
    ) -> Any:
        self.calls.append(
            {"url": url, "payload": dict(payload), "headers": dict(headers), "timeout_s": timeout_s}
        )
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def ollama_reply(content: str) -> dict[str, Any]:
    return {"model": "qwen3:8b", "message": {"role": "assistant", "content": content}, "done": True}


def gemini_reply(*texts: str, thought: str | None = None) -> dict[str, Any]:
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": t} for t in texts]
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


def prompt(task: str = "extract_query"):
    return DefaultPromptBuilder().build(TASKS[task], {"query": mask("해운대 범죄 영화")})


def run(coro):
    return asyncio.run(coro)


# ── Ollama ────────────────────────────────────────────────
def test_ollama_request_matches_poc_settings() -> None:
    post = FakePost(ollama_reply('{"a": 1}'))
    provider = OllamaProvider("http://gpu:11434/", "qwen3:8b", post=post)

    assert run(provider.complete(prompt(), timeout_s=7)) == '{"a": 1}'

    (call,) = post.calls
    body = call["payload"]
    assert call["url"] == "http://gpu:11434/api/chat"
    assert (body["model"], body["stream"], body["think"]) == ("qwen3:8b", False, False)
    assert body["options"] == {"temperature": 0.0}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][1]["content"] == "질문: 해운대 범죄 영화"
    assert "$ref" not in json.dumps(body["format"])  # 스키마를 펼쳐서 넘긴다
    assert call["timeout_s"] == 7


@pytest.mark.parametrize("reply", [{}, {"message": {}}, ollama_reply("  "), ["x"]])
def test_ollama_bad_response_is_upstream_error(reply: Any) -> None:
    provider = OllamaProvider("http://gpu:11434", "qwen3:8b", post=FakePost(reply))

    with pytest.raises(ProviderError) as e:
        run(provider.complete(prompt(), timeout_s=5))
    assert e.value.kind == "upstream"


def test_inline_refs_expands_nested_models() -> None:
    schema = TASKS["extract_query"].output.model_json_schema()
    assert "$defs" in schema  # anchor·travel이 $ref로 들어 있다

    flat = inline_refs(schema)

    text = json.dumps(flat, ensure_ascii=False)
    assert "$ref" not in text and "$defs" not in text
    anchor = flat["properties"]["anchor"]["anyOf"]
    assert any(o.get("type") == "object" for o in anchor)
    assert schema["$defs"]  # 원본은 바꾸지 않는다


# ── Gemini ────────────────────────────────────────────────
def test_gemini_sends_key_in_header_not_url() -> None:
    post = FakePost(gemini_reply('{"a":', " 1}", thought="생각 내용"))
    provider = GeminiProvider(KEY, "gemini-3.5-flash", post=post)

    assert run(provider.complete(prompt(), timeout_s=9)) == '{"a": 1}'  # 생각 파트는 뺀다

    (call,) = post.calls
    body = call["payload"]
    assert call["url"].endswith("/models/gemini-3.5-flash:generateContent")
    assert KEY not in call["url"]
    assert call["headers"] == {"x-goog-api-key": KEY}
    assert body["systemInstruction"]["parts"][0]["text"].startswith("너는")
    assert body["contents"][0]["parts"][0]["text"] == "질문: 해운대 범죄 영화"
    assert body["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.parametrize(
    "reply",
    [{"promptFeedback": {"blockReason": "SAFETY"}}, {"candidates": []}, gemini_reply("")],
)
def test_gemini_blocked_or_empty_is_upstream_error(reply: Any) -> None:
    provider = GeminiProvider(KEY, "m", post=FakePost(reply))

    with pytest.raises(ProviderError) as e:
        run(provider.complete(prompt(), timeout_s=5))
    assert e.value.kind == "upstream"


def test_gemini_key_never_in_repr() -> None:
    assert KEY not in repr(GeminiProvider(KEY, "m", post=FakePost()))
    assert KEY not in repr(AISettings(gemini_api_key=KEY))
    with pytest.raises(ValueError):
        GeminiProvider("", "m")


# ── HTTP 오류 변환 ────────────────────────────────────────
def test_status_mapping() -> None:
    limited = error_for_status(429, {"retry-after": "1.5"})
    assert (limited.kind, limited.retry_after_s) == ("rate_limited", 1.5)
    assert error_for_status(429, {}).retry_after_s is None
    for status in (400, 401, 404, 500, 503):
        error = error_for_status(status, {})
        assert error.kind == "upstream" and str(error) == f"HTTP {status}"  # 본문은 남기지 않음


def test_retry_after_http_date() -> None:
    assert retry_after_seconds("Wed, 21 Oct 2015 07:28:10 GMT", now=1445412480.0) == 10.0
    assert retry_after_seconds("곧") is None
    assert retry_after_seconds("-3") == 0.0


# ── 임베딩 ────────────────────────────────────────────────
def test_embedder_returns_vectors() -> None:
    post = FakePost({"embeddings": [[0.1] * 1024, [0.2] * 1024]})
    embedder = OllamaEmbedder("http://gpu:11434", "bge-m3", post=post)

    vectors = run(embedder.embed(["광안대교", "해운대"]))

    assert [len(v) for v in vectors] == [1024, 1024]
    assert post.calls[0]["url"] == "http://gpu:11434/api/embed"
    assert post.calls[0]["payload"] == {"model": "bge-m3", "input": ["광안대교", "해운대"]}
    assert run(embedder.embed([])) == []


@pytest.mark.parametrize(
    "reply", [{"embeddings": [[0.1] * 1024]}, {"embeddings": [[0.1] * 768] * 2}, {}]
)
def test_embedder_rejects_wrong_count_or_dimension(reply: Any) -> None:
    embedder = OllamaEmbedder("http://gpu:11434", "bge-m3", post=FakePost(reply))

    with pytest.raises(ProviderError):
        run(embedder.embed(["a", "b"]))


# ── 설정·조립 ─────────────────────────────────────────────
def test_settings_from_env_defaults() -> None:
    s = AISettings.from_env({})
    assert (s.provider, s.ollama_model, s.ollama_embed_model, s.gemini_model) == (
        "gemini",
        "qwen3:8b",
        "bge-m3",
        "gemini-3.5-flash",
    )
    assert s.gemini_api_key is None

    s = AISettings.from_env({"AI_PROVIDER": " Ollama ", "OLLAMA_BASE_URL": "http://gpu:11434"})
    assert (s.provider, s.ollama_base_url) == ("ollama", "http://gpu:11434")

    with pytest.raises(ValueError):
        AISettings.from_env({"AI_PROVIDER": "openai"})


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"AI_PROVIDER": "gemini", "GEMINI_API_KEY": KEY}, ["gemini"]),
        ({"AI_PROVIDER": "gemini", "OLLAMA_BASE_URL": "http://gpu:11434"}, []),
        ({"AI_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://gpu:11434"}, ["ollama"]),
        (
            {"AI_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://gpu:11434", "GEMINI_API_KEY": KEY},
            ["ollama", "gemini"],
        ),
        ({"AI_PROVIDER": "none", "GEMINI_API_KEY": KEY}, []),
    ],
)
def test_provider_selection(env: dict[str, str], expected: list[str]) -> None:
    from app.data.ai_gateway.factory import build_providers

    assert list(build_providers(AISettings.from_env(env), post=FakePost())) == expected


def test_ollama_mode_needs_base_url() -> None:
    with pytest.raises(ValueError):
        build_llm_client(AISettings(provider="ollama"))


def test_embedder_only_with_ollama() -> None:
    assert build_embedder(AISettings(provider="gemini")) is None
    assert build_embedder(AISettings(ollama_base_url="http://gpu:11434")) is not None


# ── 체인과 함께(작업 → 공급자 → 스키마 검증) ───────────────
def test_chain_falls_back_from_ollama_to_gemini() -> None:
    post = FakePost(
        ProviderError("timeout"),  # Ollama 시간 초과
        gemini_reply(json.dumps(CONDITIONS, ensure_ascii=False)),
    )
    settings = AISettings(provider="ollama", ollama_base_url="http://gpu:11434", gemini_api_key=KEY)
    client = build_llm_client(settings, post=post)

    result = run(client.generate("extract_query", {"query": mask("해운대 범죄 영화")}, 20))

    assert result.provider == "gemini"
    assert result.data.genres == ["범죄·느와르"]
    assert [a.outcome for a in result.attempts] == ["timeout", "ok"]
    urls = [c["url"].split("/")[-1] for c in post.calls]
    assert urls == ["chat", "gemini-3.5-flash:generateContent"]


def test_no_provider_degrades() -> None:
    client = build_llm_client(AISettings(provider="none"))

    with pytest.raises(AIUnavailable) as e:
        run(client.generate("extract_query", {"query": mask("해운대")}, 20))
    assert e.value.error_code == "AI_UNAVAILABLE"


# ── httpx 연결(가짜 전송) ──────────────────────────────────
def _httpx_post(handler):
    httpx = pytest.importorskip("httpx")
    from app.data.ai_gateway.transport import HttpxPost

    return HttpxPost(transport=httpx.MockTransport(handler)), httpx


def test_httpx_post_sends_json_and_returns_body() -> None:
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("x-goog-api-key")
        return httpx.Response(200, json={"ok": True})

    post, httpx = _httpx_post(handler)

    body = run(
        post("https://x.test/m", {"a": "한글"}, headers={"x-goog-api-key": "k"}, timeout_s=3)
    )

    assert body == {"ok": True}
    assert seen == {"body": {"a": "한글"}, "key": "k"}


@pytest.mark.parametrize(
    ("make", "kind"),
    [
        (lambda httpx: httpx.Response(429, headers={"Retry-After": "2"}), "rate_limited"),
        (lambda httpx: httpx.Response(503, text="입력 문장이 되돌아온 본문"), "upstream"),
        (lambda httpx: httpx.Response(200, text="<html>"), "upstream"),
    ],
)
def test_httpx_post_maps_errors(make, kind: str) -> None:
    post, httpx = _httpx_post(lambda request: make(httpx))

    with pytest.raises(ProviderError) as e:
        run(post("https://x.test/m?key=비밀", {}, headers={}, timeout_s=3))
    assert e.value.kind == kind
    assert "비밀" not in str(e.value) and "되돌아온" not in str(e.value)


@pytest.mark.parametrize(
    ("exc", "kind"), [("ReadTimeout", "timeout"), ("ConnectError", "upstream")]
)
def test_httpx_post_maps_network_failures(exc: str, kind: str) -> None:
    def handler(request):
        raise getattr(httpx, exc)("https://x.test/m?key=비밀", request=request)

    post, httpx = _httpx_post(handler)

    with pytest.raises(ProviderError) as e:
        run(post("https://x.test/m?key=비밀", {}, headers={}, timeout_s=3))
    assert e.value.kind == kind
    assert "비밀" not in str(e.value)
