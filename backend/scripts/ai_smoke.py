"""AI 공급자 연결 확인 (개발용, 서비스 코드에서 쓰지 않음).

저장소 루트 .env의 AI_PROVIDER·GEMINI_API_KEY·OLLAMA_BASE_URL을 읽어 실제로 한 번 부른다.
키 값은 출력하지 않는다. backend 폴더에서:

    python -m scripts.ai_smoke                       # 조건 추출(extract_query) 1회
    python -m scripts.ai_smoke "광안대교 야경 나온 영화"
    python -m scripts.ai_smoke --embed               # 임베딩(Ollama 있을 때만)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.data.ai_gateway import AISettings, AIUnavailable, build_embedder, build_llm_client
from app.support.privacy import mask

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
SAMPLE = "해운대 근처에서 찍은 범죄 영화 촬영지 알려줘"


def load_dotenv(path: Path = ROOT_ENV) -> None:
    """KEY=값 줄만 읽는다. 이미 있는 환경변수는 덮어쓰지 않는다. 값 뒤 ' #' 이후는 주석."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


async def run(query: str, embed: bool) -> int:
    settings = AISettings.from_env()
    print(settings)
    if embed:
        embedder = build_embedder(settings)
        if embedder is None:
            print("OLLAMA_BASE_URL이 없어 임베딩을 건너뜁니다")
            return 1
        vectors = await embedder.embed([query])
        print(f"임베딩 성공: {len(vectors)}개, 차원 {len(vectors[0])}")
        return 0

    client = build_llm_client(settings)
    masked = mask(query)
    print(f"질문(마스킹 후): {masked.text}")
    try:
        result = await client.generate("extract_query", {"query": masked}, deadline_s=25)
    except AIUnavailable as e:
        print(f"강등: {e.error_code}")
        for a in e.attempts:
            print(f"  - {a.provider}: {a.outcome} ({a.latency_ms}ms)")
        return 1
    print(f"성공: {result.provider}, {result.latency_ms}ms")
    for a in result.attempts:
        print(f"  - {a.provider}: {a.outcome} ({a.latency_ms}ms)")
    print(json.dumps(result.data.model_dump(), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 공급자 연결 확인")
    parser.add_argument("query", nargs="?", default=SAMPLE)
    parser.add_argument("--embed", action="store_true", help="임베딩 확인")
    args = parser.parse_args()
    load_dotenv()
    raise SystemExit(asyncio.run(run(args.query, args.embed)))


if __name__ == "__main__":
    main()
