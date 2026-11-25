"""사전 임베딩 단계 테스트 (FR-617). 실제 Ollama·DB 없이 가짜 연결·임베더로 본다."""

import asyncio

import pytest

import app.batch.__main__ as batch_main
from app.batch.__main__ import cli
from app.batch.embed import EmbedStats, fill_embeddings, vector_text


class FakeConn:
    def __init__(self, surfaces: list[str]) -> None:
        self.rows = [{"id": i, "surface": s} for i, s in enumerate(surfaces, start=1)]
        self.updated: dict[int, str] = {}

    async def fetch(self, sql: str):
        assert "embedding IS NULL" in sql
        return self.rows

    async def executemany(self, sql: str, args) -> None:
        assert sql.startswith("UPDATE dict_entry SET embedding")
        self.updated.update(dict(args))


class FakeEmbedder:
    def __init__(self, fail_on: set[int] = frozenset()) -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    async def embed(self, texts):
        self.calls.append(list(texts))
        if len(self.calls) in self.fail_on:
            raise RuntimeError("공급자 오류")
        return [[float(len(t)), 0.5] for t in texts]


def test_vector_text() -> None:
    assert vector_text([1, 0.25]) == "[1.0,0.25]"


def test_fill_in_batches() -> None:
    conn = FakeConn([f"이름{i}" for i in range(5)])
    emb = FakeEmbedder()
    stats = asyncio.run(fill_embeddings(conn, emb, batch=2))
    assert [len(c) for c in emb.calls] == [2, 2, 1]
    assert stats == EmbedStats(targets=5, filled=5, failed_batches=0)
    assert conn.updated[1] == "[3.0,0.5]"


def test_failed_batch_is_skipped_and_rest_filled() -> None:
    conn = FakeConn(["가", "나", "다"])
    stats = asyncio.run(fill_embeddings(conn, FakeEmbedder(fail_on={1}), batch=2))
    assert stats.as_dict() == {"targets": 3, "filled": 1, "failed_batches": 1}
    assert set(conn.updated) == {3}  # 실패한 묶음(1·2번)은 비워 두고 다음 실행에서 다시


def test_nothing_to_fill() -> None:
    emb = FakeEmbedder()
    assert asyncio.run(fill_embeddings(FakeConn([]), emb)).targets == 0
    assert emb.calls == []


@pytest.mark.parametrize("missing", ["DATABASE_URL", "OLLAMA_BASE_URL"])
def test_embed_only_needs_db_and_ollama(monkeypatch, capsys, tmp_path, missing) -> None:
    monkeypatch.setattr(batch_main, "ROOT_ENV", tmp_path / "no-root.env")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.delenv(missing)
    assert cli(["--embed-only"]) == 2
    assert f"{missing} 값이 없습니다" in capsys.readouterr().err
