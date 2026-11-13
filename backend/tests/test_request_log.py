"""요청 로그 테스트 (NFR-003·005, ERR-016)."""

import json
import logging
import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.request_log import KST, DailyJsonlWriter
from app.core.config import Settings
from app.main import _set_app_log_level, create_app
from app.support import request_context
from app.support.privacy import mask

HAEUNDAE = {"lat": 35.15871, "lon": 129.16042}


def _app(log_dir: Path) -> FastAPI:
    app = create_app(Settings(database_url=None, log_dir=str(log_dir)))

    @app.post("/api/test/ai")
    async def ai_route() -> dict:  # F4 서비스가 하듯 AI 시도와 마스킹한 질의를 넘긴다
        request_context.note_ai_attempts(
            [
                SimpleNamespace(provider="ollama", outcome="timeout"),
                SimpleNamespace(provider="gemini", outcome="schema"),
                SimpleNamespace(provider="gemini", outcome="ok"),
                SimpleNamespace(provider="ollama", outcome="circuit_open"),  # 부르지 않음
            ]
        )
        request_context.note_masked_query(mask("해운대 영화 010-1234-5678로 알려줘"))
        return {"ok": True}

    @app.get("/api/test/boom")
    async def boom() -> dict:
        raise RuntimeError("연락처 010-9876-5432 처리 실패")

    return app


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def client(log_dir: Path) -> Iterator[TestClient]:
    yield TestClient(_app(log_dir), raise_server_exceptions=False)


def _records(log_dir: Path) -> list[dict]:
    files = sorted(log_dir.glob("requests-*.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


def test_records_template_status_and_latency(client: TestClient, log_dir: Path) -> None:
    res = client.get("/api/locations/1")

    (rec,) = _records(log_dir)
    assert set(rec) == {"ts", "request_id", "method", "path", "status", "latency_ms"}
    assert rec["path"] == "/api/locations/{location_id}"  # 실제 값(1) 대신 경로 틀
    assert (rec["method"], rec["status"]) == ("GET", 200)
    assert rec["latency_ms"] >= 0
    assert res.headers["X-Request-Id"] == rec["request_id"]
    assert re.fullmatch(r"[0-9a-f]{16}", rec["request_id"])


def test_location_query_string_and_body_are_not_logged(client: TestClient, log_dir: Path) -> None:
    client.post("/api/locations/search?lat=35.15871&token=abc", json={"base": HAEUNDAE})

    text = next(log_dir.glob("*.jsonl")).read_text(encoding="utf-8")
    for leaked in ("35.15871", "129.16042", "token", "abc", "base"):
        assert leaked not in text  # NFR-003: 좌표·쿼리 문자열·본문 없음
    assert _records(log_dir)[0]["path"] == "/api/locations/search"


def test_incoming_request_id_is_ignored(client: TestClient, log_dir: Path) -> None:
    res = client.get("/api/meta", headers={"X-Request-Id": "evil\nfake-line"})

    assert res.headers["X-Request-Id"] != "evil\nfake-line"
    assert "evil" not in next(log_dir.glob("*.jsonl")).read_text(encoding="utf-8")


def test_error_codes_are_recorded(client: TestClient, log_dir: Path) -> None:
    client.get("/api/nope/secret-path")
    client.post("/api/locations/search", json={"base": {"lat": 135, "lon": 129}})

    unknown, invalid = _records(log_dir)
    assert (unknown["status"], unknown["error_code"], unknown["path"]) == (404, "NOT_FOUND", None)
    assert (invalid["status"], invalid["error_code"]) == (422, "INVALID_REQUEST")
    assert "secret-path" not in json.dumps(unknown)  # 맞는 라우트가 없으면 경로를 남기지 않음


def test_ai_calls_and_masked_query(client: TestClient, log_dir: Path) -> None:
    client.post("/api/test/ai")

    (rec,) = _records(log_dir)
    assert rec["ai_calls"] == {"ollama": 1, "gemini": 2}
    assert rec["query"] == "해운대 영화 [개인정보]로 알려줘"


def test_masked_query_rejects_raw_text() -> None:
    with pytest.raises(TypeError):
        request_context.note_masked_query("해운대 영화")  # type: ignore[arg-type]


def test_notes_outside_request_do_nothing() -> None:
    request_context.note_ai_attempts([SimpleNamespace(provider="gemini", outcome="ok")])
    request_context.note_masked_query(mask("배치"))
    request_context.note_error("X")
    assert request_context.current() is None


def test_unhandled_error_returns_500_with_request_id(
    client: TestClient, log_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        res = client.get("/api/test/boom")

    rec = _records(log_dir)[0]
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "INTERNAL_ERROR"
    assert res.headers["X-Request-Id"] == rec["request_id"]
    assert rec["request_id"] in res.json()["error"]["message"]
    assert (rec["status"], rec["error_code"]) == (500, "INTERNAL_ERROR")
    (logged,) = [r for r in caplog.records if r.name == "app.api.errors"]
    assert rec["request_id"] in logged.getMessage() and logged.exc_info  # 스택 추적은 남김
    assert "010-9876-5432" not in res.text


def test_no_log_dir_only_adds_request_id(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(database_url=None)))

    res = client.get("/api/meta")

    assert res.status_code == 200 and res.headers["X-Request-Id"]
    assert not list(tmp_path.iterdir())


def test_log_write_failure_does_not_break_response(tmp_path: Path) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("폴더 자리에 파일이 있음")
    client = TestClient(create_app(Settings(database_url=None, log_dir=str(blocker))))

    assert client.get("/api/meta").status_code == 200


def test_writer_keeps_seven_days(tmp_path: Path) -> None:
    for day in ("2026-11-01", "2026-11-06", "2026-11-07", "2026-11-12"):
        (tmp_path / f"requests-{day}.jsonl").write_text("{}\n")
    (tmp_path / "other.txt").write_text("남겨 둠")
    writer = DailyJsonlWriter(tmp_path, now=lambda: datetime(2026, 11, 13, 9, tzinfo=KST))

    writer.write({"status": 200})

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "other.txt",
        "requests-2026-11-07.jsonl",  # 11/7~11/13 7일치만 남김
        "requests-2026-11-12.jsonl",
        "requests-2026-11-13.jsonl",
    ]
    removed = writer.prune(date(2026, 11, 19))  # 11/13~11/19만 남김
    assert sorted(removed) == ["requests-2026-11-07.jsonl", "requests-2026-11-12.jsonl"]


def test_writer_starts_new_file_when_date_changes(tmp_path: Path) -> None:
    now = [datetime(2026, 11, 13, 23, 59, tzinfo=KST)]
    writer = DailyJsonlWriter(tmp_path, now=lambda: now[0])

    writer.write({"n": 1})
    now[0] = datetime(2026, 11, 14, 0, 1, tzinfo=KST)
    writer.write({"n": 2})

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "requests-2026-11-13.jsonl",
        "requests-2026-11-14.jsonl",
    ]


@pytest.mark.parametrize(("value", "expected"), [("WARNING", logging.WARNING), ("bad", 20)])
def test_app_log_level(value: str, expected: int) -> None:
    _set_app_log_level(value)
    try:
        assert logging.getLogger("app").level == expected
    finally:
        _set_app_log_level("INFO")
