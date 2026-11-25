"""사전 임베딩 채우기 (FR-617, FR-303, ADR-008, COMP-13).

dict_entry에서 embedding이 빈 행의 표기(surface)를 로컬 Ollama bge-m3(1024차원)로 임베딩해 채운다.
F3 사전 대조는 정확 일치·트라이그램으로 못 찾은 이름을 이 벡터로 한 번 더 찾는다(코사인 0.75).

- 동기화 반영이 성공한 뒤의 선택 단계다(진입점 --embed). 실패해도 동기화 결과는 그대로 둔다.
- 사전 재구성(dictionary.rebuild)은 바뀌지 않은 행을 그대로 두므로 다음 실행에서는 새 행만 채운다.
- 표기(공공데이터·KMDb의 이름)만 로컬 모델로 보낸다. 사용자 입력이 아니라 마스킹 대상이 없다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import asyncpg

from app.data.ai_gateway import Embedder

log = logging.getLogger(__name__)
BATCH = 64  # 한 번에 보내는 표기 수


@dataclass
class EmbedStats:
    targets: int = 0  # 비어 있던 행
    filled: int = 0  # 채운 행
    failed_batches: int = 0  # 실패한 묶음(그 행은 다음 실행에서 다시 시도)

    def as_dict(self) -> dict[str, int]:
        return {
            "targets": self.targets,
            "filled": self.filled,
            "failed_batches": self.failed_batches,
        }


def vector_text(vec: Sequence[float]) -> str:
    """pgvector 입력 문자열 '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def fill_embeddings(
    conn: asyncpg.Connection, embedder: Embedder, *, batch: int = BATCH
) -> EmbedStats:
    """embedding이 빈 dict_entry 행을 채운다. 묶음 하나가 실패하면 건너뛰고 다음 묶음을 한다."""
    rows = await conn.fetch(
        "SELECT id, surface FROM dict_entry WHERE embedding IS NULL ORDER BY id"
    )
    stats = EmbedStats(targets=len(rows))
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        try:
            vectors = await embedder.embed([r["surface"] for r in chunk])
        except Exception as e:  # 공급자 오류·형식 이상: 이 묶음만 건너뛴다
            stats.failed_batches += 1
            log.warning("사전 임베딩 묶음 실패(%d행): %s", len(chunk), type(e).__name__)
            continue
        await conn.executemany(
            "UPDATE dict_entry SET embedding = $2::text::vector WHERE id = $1",
            [(r["id"], vector_text(v)) for r, v in zip(chunk, vectors, strict=True)],
        )
        stats.filled += len(chunk)
    log.info("사전 임베딩: 대상 %d, 채움 %d", stats.targets, stats.filled)
    return stats
