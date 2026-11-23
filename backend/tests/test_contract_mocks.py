"""계약 대조 2단계 (#115, 계획서 5.2 통합 테스트): 화면 목업 응답 ↔ 백엔드 응답 스키마.

프론트 목업은 화면 타입(frontend/src/api/*.ts)에 맞춰 만든 응답이다. 그 응답을
실제 라우트의 response_model에 넣어 보면 두 쪽 형식이 어긋난 곳을 찾을 수 있다.
- 스키마 검사 실패: 화면이 받을 값이 백엔드 형식과 다르다(빠진 필드, 다른 타입·값)
- 목업에만 있는 키: 화면은 기대하지만 백엔드는 보내지 않는 필드

목업 응답 파일은 프론트에서 만든다(파일이 없으면 이 테스트는 건너뛴다):
    cd frontend && CONTRACT_OUT=../contract-mocks.json npx vitest run src/contract
    cd backend && CONTRACT_MOCKS=../contract-mocks.json python -m pytest \
        tests/test_contract_mocks.py
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError

from app.main import app

FILE = os.environ.get("CONTRACT_MOCKS")
pytestmark = pytest.mark.skipif(not FILE, reason="CONTRACT_MOCKS 없음(프론트 목업 응답 파일)")


def _cases() -> list[dict[str, Any]]:
    if not FILE:
        return []
    return json.loads(Path(FILE).read_text(encoding="utf-8"))


def _route(method: str, path: str) -> APIRoute:
    full = "/api" + path.split("?")[0]
    for r in app.routes:
        if isinstance(r, APIRoute) and method in r.methods and r.path_regex.match(full):
            return r
    raise AssertionError(f"{method} {full}: 백엔드에 이 API가 없습니다")


def _resolve(schema: dict, defs: dict) -> dict:
    while "$ref" in schema:
        schema = defs[schema["$ref"].split("/")[-1]]
    return schema


def extra_keys(data: Any, schema: dict, defs: dict, at: str = "$") -> list[str]:
    """data에는 있는데 스키마 properties에는 없는 키(목업에만 있는 키)를 모은다."""
    schema = _resolve(schema, defs)
    found: list[str] = []
    for alt in schema.get("anyOf", []) + schema.get("oneOf", []):
        alt = _resolve(alt, defs)
        if alt.get("type") == "null" and data is None:
            return []
        if isinstance(data, dict) and alt.get("type") == "object":
            return extra_keys(data, alt, defs, at)
        if isinstance(data, list) and alt.get("type") == "array":
            return extra_keys(data, alt, defs, at)
    if isinstance(data, dict) and "properties" in schema:
        props = schema["properties"]
        for k, v in data.items():
            if k not in props:
                if not schema.get("additionalProperties"):
                    found.append(f"{at}.{k}")
            else:
                found += extra_keys(v, props[k], defs, f"{at}.{k}")
    elif isinstance(data, list) and "items" in schema:
        for i, v in enumerate(data[:3]):
            found += extra_keys(v, schema["items"], defs, f"{at}[{i}]")
    return found


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_mock_response_matches_backend_schema(case: dict[str, Any]) -> None:
    route = _route(case["method"], case["path"])
    if route.response_model is None:
        return
    adapter = TypeAdapter(route.response_model)
    try:
        adapter.validate_python(case["data"])
    except ValidationError as e:
        pytest.fail(f"{case['name']}: 화면 목업이 백엔드 형식과 다릅니다\n{e}")
    schema = adapter.json_schema(ref_template="#/$defs/{model}")
    extras = extra_keys(case["data"], schema, schema.get("$defs", {}))
    assert not extras, f"{case['name']}: 백엔드가 보내지 않는 필드를 화면이 기대합니다 {extras}"
