"""C1 배치 실행: backend 폴더에서 python -m app.batch --once [--out 결과.json]

환경변수 이름은 저장소 루트 .env.example과 같다. 저장소 루트 .env가 있으면 읽는다
(scripts/ai_smoke.py와 같음, 이미 있는 환경변수는 덮지 않음). 다른 파일은 --env-file로 준다.
  필수: DATABASE_URL, DATA_GO_KR_SERVICE_KEY
  좌표: JUSO_SEARCH_API_KEY·JUSO_COORD_API_KEY(정확한 주소). 주소가 없거나 동까지만 있는
        촬영지는 GEOCODE_KAKAO_KEYWORD=true이고 KAKAO_REST_API_KEY가 있을 때만 카카오 키워드
        검색으로 찾는다(#25, 기본 끔 → 위치 미확인, ADR-006).
        키가 없으면 그 좌표만 비워 두고 계속한다.
  매칭: KMDB_SERVICE_KEY. 없으면 KMDB_CSV_PATH('영화기본정보' CSV)로 매칭한다.
  선택: JUSO_BASE_URL(주소 API 주소를 바꿀 때)
정기 실행(FR-616)은 아직 없고 --once 수동 실행만 한다.

AI 제안(FR-608, 선택): --ai-review를 주면 반영이 성공한 뒤 태그 없는 촬영지와 검수 대기 작품에
AI 제안을 받아 검수 큐에 '미검수'로 넣는다. 공급자는 AI_PROVIDER(로컬 모델이 있는 곳에서
ollama). Gemini 무료 한도(하루 20회)로는 촬영지 수백 곳을 못 돌리므로 로컬 모델에서 켠다.
--ai-tag-limit N으로 한 번에 묻는 촬영지 수를 줄일 수 있다.

로그: 로그 설정 뒤 PrivacyLogFilter를 붙이고(#39), httpx·httpcore는 WARNING으로 올린다.
httpx는 요청 URL(serviceKey·confmKey 포함)을 INFO로 남기기 때문이다.
출력·결과 파일은 UTF-8로 쓴다(#26, Windows 콘솔·파일에서 한글이 깨지지 않게).
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from collections.abc import MutableMapping
from pathlib import Path

import asyncpg
import httpx

from app.batch import job, kmdb
from app.batch.geocode import DEFAULT_BASE, JusoGeocoder, keyword_geocoder
from app.data.ai_gateway.factory import AISettings, build_llm_client
from app.data.ai_gateway.store import PgUsageCounter
from app.support.privacy import install_privacy_log_filter

log = logging.getLogger("app.batch")
REQUIRED = ("DATABASE_URL", "DATA_GO_KR_SERVICE_KEY")
_TRUE = ("1", "true", "yes", "on")
_COMMENT = re.compile(r"(?:^|\s)#")
ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
ENV_HINT = "저장소 루트의 .env.example을 복사해 .env를 만들고 값을 채우세요"


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level)
    install_privacy_log_filter()  # 핸들러를 만든 뒤 붙인다
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def load_env_file(path: Path, env: MutableMapping[str, str] = os.environ) -> None:
    """KEY=값 줄을 읽어 env에 없는 이름만 채운다. 값은 출력하지 않는다.

    따옴표 없는 값은 '#'(맨 앞이거나 앞이 공백) 뒤를 주석으로 본다(.env.example의 설명 주석).
    """
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = (s.strip() for s in line.split("=", 1))
        if value[:1] in ("'", '"') and value.count(value[0]) >= 2:
            value = value[1 : value.index(value[0], 1)]
        else:
            value = _COMMENT.split(value, 1)[0].strip()
        if name and name not in env:
            env[name] = value


def _env(env: MutableMapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def missing(env: MutableMapping[str, str] = os.environ) -> list[str]:
    return [n for n in REQUIRED if not _env(env, n)]


def build_clients(http: httpx.AsyncClient, env: MutableMapping[str, str] = os.environ):
    """환경변수로 외부 연결을 만든다. 빠진 선택 키는 이름만 경고로 남긴다."""
    skey, ckey = _env(env, "JUSO_SEARCH_API_KEY"), _env(env, "JUSO_COORD_API_KEY")
    juso = (
        JusoGeocoder(http, skey, ckey, _env(env, "JUSO_BASE_URL") or DEFAULT_BASE)
        if skey and ckey
        else None
    )
    keyword_on = _env(env, "GEOCODE_KAKAO_KEYWORD").lower() in _TRUE  # config.py와 같은 규칙
    kakao = keyword_geocoder(http, keyword_on, _env(env, "KAKAO_REST_API_KEY"))
    source: kmdb.CandidateSource | None = None
    if _env(env, "KMDB_SERVICE_KEY"):
        source = kmdb.KmdbApi(http, _env(env, "KMDB_SERVICE_KEY"))
    elif _env(env, "KMDB_CSV_PATH"):
        source = kmdb.KmdbCsv.load(_env(env, "KMDB_CSV_PATH"))
    if juso is None:
        log.warning("JUSO_SEARCH_API_KEY·JUSO_COORD_API_KEY가 없어 주소 좌표를 찾지 않습니다")
    if not keyword_on:
        log.info("GEOCODE_KAKAO_KEYWORD가 꺼져 있어 주소 없는 촬영지는 위치 미확인으로 둡니다")
    elif kakao is None:
        log.warning("KAKAO_REST_API_KEY가 없어 주소 없는 촬영지 좌표를 찾지 않습니다")
    if source is None:
        log.warning("KMDB_SERVICE_KEY·KMDB_CSV_PATH가 없어 KMDb 매칭을 하지 않습니다")
    return job.Clients(http, _env(env, "DATA_GO_KR_SERVICE_KEY"), juso, kakao, source)


def build_ai(env: MutableMapping[str, str] = os.environ):
    """FR-608 단계용 AI 클라이언트. 공급자가 none이면 None(단계를 건너뜀)."""
    settings = AISettings.from_env(env)
    if settings.provider == "none":
        log.warning("AI_PROVIDER=none이라 AI 제안 단계를 건너뜁니다")
        return None
    if settings.provider == "gemini":
        log.warning(
            "Gemini 무료 한도로는 일부만 제안받습니다. 로컬 모델(AI_PROVIDER=ollama)을 권합니다"
        )
    return build_llm_client(settings, usage=PgUsageCounter())  # Gemini 하루 한도를 지킨다


async def main(
    trigger: str, out: Path | None, ai_review: bool = False, ai_tag_limit: int | None = None
) -> int:
    if names := missing():
        print(f"{', '.join(names)} 값이 없습니다. {ENV_HINT}", file=sys.stderr)
        return 2
    async with httpx.AsyncClient() as http:
        clients = build_clients(http)
        if ai_review:
            clients.ai = build_ai()
            clients.ai_tag_limit = ai_tag_limit
        conn = await asyncpg.connect(_env(os.environ, "DATABASE_URL"))
        try:
            run_id, counts, stats = await job.run(conn, clients, trigger)
            status = await conn.fetchval("SELECT status FROM sync_run WHERE id = $1", run_id)
        finally:
            await conn.close()
    result = {
        "run_id": run_id,
        "status": status,
        "counts": counts.model_dump(),
        "stats": stats.__dict__,
    }
    text = json.dumps(result, ensure_ascii=False, indent=1)
    print(text)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
    return 0 if status == "succeeded" else 1


def cli(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m app.batch")
    p.add_argument("--once", action="store_true", required=True, help="동기화 한 번 실행")
    p.add_argument("--trigger", choices=["manual", "schedule"], default="manual")
    p.add_argument("--env-file", type=Path, help="KEY=값 파일. 없으면 저장소 루트 .env")
    p.add_argument("--out", type=Path, help="결과 JSON을 UTF-8 파일로도 저장")
    p.add_argument("--ai-review", action="store_true", help="반영 뒤 AI 제안 단계(FR-608)도 실행")
    p.add_argument("--ai-tag-limit", type=int, help="AI에 태그를 물을 촬영지 수(없으면 전부)")
    a = p.parse_args(argv)
    if a.env_file and not a.env_file.is_file():
        print(f"{a.env_file} 파일이 없습니다. {ENV_HINT}", file=sys.stderr)
        return 2
    if (env_file := a.env_file or ROOT_ENV).is_file():
        load_env_file(env_file)
    setup_logging(_env(os.environ, "LOG_LEVEL") or "INFO")
    return asyncio.run(main(a.trigger, a.out, a.ai_review, a.ai_tag_limit))


if __name__ == "__main__":
    sys.exit(cli())
