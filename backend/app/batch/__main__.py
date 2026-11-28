"""C1 배치 실행: backend 폴더에서 python -m app.batch --once [--out 결과.json]
감시 실행(API-021, FR-616): python -m app.batch --watch
  1분마다 관리자 수동 요청(sync_run 'requested')을 가져가 실행하고, 요청이 없으면
  SYNC_INTERVAL_DAYS(기본 7일, 0이면 끔)마다 정기 동기화를 돌린다(ADR-012: API와는 DB로만 통신).

환경변수 이름은 저장소 루트 .env.example과 같다. 저장소 루트 .env가 있으면 읽는다
(scripts/ai_smoke.py와 같음, 이미 있는 환경변수는 덮지 않음). 다른 파일은 --env-file로 준다.
  필수: DATABASE_URL, DATA_GO_KR_SERVICE_KEY
  좌표: JUSO_SEARCH_API_KEY·JUSO_COORD_API_KEY(정확한 주소). 주소가 없거나 동까지만 있는
        촬영지는 GEOCODE_KAKAO_KEYWORD=true이고 KAKAO_REST_API_KEY가 있을 때만 카카오 키워드
        검색으로 찾는다(#25, 기본 끔 → 위치 미확인, ADR-006).
        키가 없으면 그 좌표만 비워 두고 계속한다.
  매칭: KMDB_SERVICE_KEY. 없으면 KMDB_CSV_PATH('영화기본정보' CSV)로 매칭한다.
  선택: JUSO_BASE_URL(주소 API 주소를 바꿀 때)
--once는 지금 한 번만 돌고 끝난다(관리자 요청과 상관없음).

AI 제안(FR-608, 선택): --ai-review를 주면 반영이 성공한 뒤 태그 없는 촬영지와 검수 대기 작품에
AI 제안을 받아 검수 큐에 '미검수'로 넣는다. 공급자는 AI_PROVIDER(로컬 모델이 있는 곳에서
ollama). Gemini 무료 한도(하루 20회)로는 촬영지 수백 곳을 못 돌리므로 로컬 모델에서 켠다.
--ai-tag-limit N으로 한 번에 묻는 촬영지 수를 줄일 수 있다.

사전 임베딩(FR-617, 선택): --embed를 주면 반영이 성공한 뒤 비어 있는 dict_entry 벡터를 로컬 Ollama
bge-m3로 채운다(OLLAMA_BASE_URL 필요, 없으면 건너뜀). 동기화 없이 지금 DB의 사전만 채우려면
--embed-only(DATABASE_URL·OLLAMA_BASE_URL만 필요).
규칙 태그(FR-607): 동기화 반영 때 태그 없는 촬영지에 장소명 키워드로 붙인다. 동기화 없이 지금 DB
(예: 시드)에만 붙이려면 --tags-only(DATABASE_URL만 필요, 외부 API를 부르지 않음).
장소 사진(FR-207): 동기화 반영 뒤 TourAPI 공공누리 제1유형 대표 이미지 주소를 촬영지에 붙인다.
동기화 없이 지금 DB에만 붙이려면 --photos-only(DATABASE_URL·DATA_GO_KR_SERVICE_KEY만 필요,
TourAPI 부산 목록 몇 쪽만 부름).

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import httpx

from app.batch import embed, job, kmdb, photos
from app.batch.geocode import DEFAULT_BASE, JusoGeocoder, keyword_geocoder
from app.data import db, rule_tags, sync_runs
from app.data.ai_gateway.factory import AISettings, build_embedder, build_llm_client
from app.data.ai_gateway.store import PgUsageCounter
from app.data.tourapi import TourApiClient
from app.support.privacy import install_privacy_log_filter

log = logging.getLogger("app.batch")
REQUIRED = ("DATABASE_URL", "DATA_GO_KR_SERVICE_KEY")
_TRUE = ("1", "true", "yes", "on")
_COMMENT = re.compile(r"(?:^|\s)#")
ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
ENV_HINT = "저장소 루트의 .env.example을 복사해 .env를 만들고 값을 채우세요"
POLL_S = 60  # 관리자 요청 확인 주기(ADR-012: 수동 동기화가 최대 1분 늦게 시작)
DEFAULT_INTERVAL_DAYS = 7  # FR-616 기본 주 1회


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
    clients = job.Clients(http, _env(env, "DATA_GO_KR_SERVICE_KEY"), juso, kakao, source)
    clients.tourapi = TourApiClient(_env(env, "DATA_GO_KR_SERVICE_KEY"))  # FR-207 장소 사진
    return clients


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


def build_embed(env: MutableMapping[str, str] = os.environ):
    """FR-617 단계용 임베더. 로컬 Ollama 주소가 없으면 None(단계를 건너뜀)."""
    embedder = build_embedder(AISettings.from_env(env))
    if embedder is None:
        log.warning("OLLAMA_BASE_URL이 없어 사전 임베딩 단계를 건너뜁니다")
    return embedder


async def sync_once(
    trigger: str,
    run_id: int | None = None,
    ai_review: bool = False,
    ai_tag_limit: int | None = None,
    embed_step: bool = False,
) -> dict:
    """동기화 한 번. run_id가 있으면 관리자 요청으로 잡아 둔 기록을 이어 쓴다."""
    async with httpx.AsyncClient() as http:
        clients = build_clients(http)
        if ai_review:
            clients.ai = build_ai()
            clients.ai_tag_limit = ai_tag_limit
        if embed_step:
            clients.embedder = build_embed()
        conn = await asyncpg.connect(_env(os.environ, "DATABASE_URL"))
        try:
            run_id, counts, stats = await job.run(conn, clients, trigger, run_id)
            status = await conn.fetchval("SELECT status FROM sync_run WHERE id = $1", run_id)
        finally:
            await conn.close()
            if ai_review:
                await db.close_pool()  # AI 호출 수 집계(PgUsageCounter)가 연 풀을 닫는다(#92 리뷰)
    return {
        "run_id": run_id,
        "status": status,
        "counts": counts.model_dump(),
        "stats": stats.__dict__,
    }


def interval_days(env: MutableMapping[str, str] = os.environ) -> int:
    """SYNC_INTERVAL_DAYS(일). 0이면 정기 실행 끔, 비었거나 틀리면 기본 7일."""
    raw = _env(env, "SYNC_INTERVAL_DAYS")
    if not raw:
        return DEFAULT_INTERVAL_DAYS
    if raw.isdigit():
        return int(raw)
    log.warning("SYNC_INTERVAL_DAYS 값이 숫자가 아니라 %d일로 둡니다", DEFAULT_INTERVAL_DAYS)
    return DEFAULT_INTERVAL_DAYS


def schedule_due(last_started: datetime | None, now: datetime, days: int) -> bool:
    """정기 동기화를 돌릴 때인지(FR-616). 수동 실행도 마지막 실행으로 친다."""
    return days > 0 and (last_started is None or now - last_started >= timedelta(days=days))


async def next_job(conn: asyncpg.Connection, days: int, now: datetime) -> tuple[str, int | None]:
    """이번에 할 일: ('manual', run_id) · ('schedule', None) · ('idle', None)."""
    if await sync_runs.fail_stale(conn):
        log.warning("멈춘 동기화 기록을 실패로 닫았습니다")
    run_id = await sync_runs.claim_requested(conn)
    if run_id is not None:
        return "manual", run_id
    if schedule_due(await sync_runs.last_started_at(conn), now, days):
        return "schedule", None
    return "idle", None


async def watch(
    ai_review: bool = False, ai_tag_limit: int | None = None, embed_step: bool = False
) -> int:
    """관리자 요청·정기 실행을 기다리며 계속 돈다(Ctrl+C로 끝)."""
    if names := missing():
        print(f"{', '.join(names)} 값이 없습니다. {ENV_HINT}", file=sys.stderr)
        return 2
    days = interval_days()
    log.info("동기화 감시 시작: 요청 확인 %d초마다, 정기 %s", POLL_S, f"{days}일" if days else "끔")
    while True:
        try:
            conn = await asyncpg.connect(_env(os.environ, "DATABASE_URL"))
            try:
                kind, run_id = await next_job(conn, days, datetime.now(timezone.utc))
            finally:
                await conn.close()
            if kind != "idle":
                log.info("동기화 시작(%s)", "관리자 요청" if kind == "manual" else "정기")
                result = await sync_once(kind, run_id, ai_review, ai_tag_limit, embed_step)
                log.info("동기화 %s 결과: %s", result["run_id"], result["status"])
        except Exception:  # 한 번 실패해도 감시는 계속한다. 실패는 sync_run에 남는다
            log.exception("동기화 중 오류가 났습니다")
        await asyncio.sleep(POLL_S)


async def embed_only(out: Path | None) -> int:
    """동기화 없이 지금 DB의 사전 임베딩만 채운다(FR-617, 시드 갱신용)."""
    if not _env(os.environ, "DATABASE_URL"):
        print(f"DATABASE_URL 값이 없습니다. {ENV_HINT}", file=sys.stderr)
        return 2
    embedder = build_embed()
    if embedder is None:
        print(f"OLLAMA_BASE_URL 값이 없습니다. {ENV_HINT}", file=sys.stderr)
        return 2
    conn = await asyncpg.connect(_env(os.environ, "DATABASE_URL"))
    try:
        stats = await embed.fill_embeddings(conn, embedder)
    finally:
        await conn.close()
    text = json.dumps({"embeddings": stats.as_dict()}, ensure_ascii=False, indent=1)
    print(text)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
    return 0 if stats.failed_batches == 0 else 1


async def tags_only(out: Path | None) -> int:
    """동기화 없이 지금 DB의 태그 없는 촬영지에 규칙 태그만 붙인다(FR-607, 시드 갱신용)."""
    if not _env(os.environ, "DATABASE_URL"):
        print(f"DATABASE_URL 값이 없습니다. {ENV_HINT}", file=sys.stderr)
        return 2
    conn = await asyncpg.connect(_env(os.environ, "DATABASE_URL"))
    try:
        async with conn.transaction():
            stats = await rule_tags.apply(conn)
    finally:
        await conn.close()
    text = json.dumps({"rule_tags": stats}, ensure_ascii=False, indent=1)
    print(text)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
    return 0


async def photos_only(out: Path | None) -> int:
    """동기화 없이 지금 DB의 촬영지에 장소 사진만 다시 붙인다(FR-207, 시드 갱신용)."""
    if names := [n for n in REQUIRED if not _env(os.environ, n)]:
        print(f"{', '.join(names)} 값이 없습니다. {ENV_HINT}", file=sys.stderr)
        return 2
    conn = await asyncpg.connect(_env(os.environ, "DATABASE_URL"))
    try:
        client = TourApiClient(_env(os.environ, "DATA_GO_KR_SERVICE_KEY"))
        stats = await photos.refresh(conn, client)
    finally:
        await conn.close()
    text = json.dumps({"photos": stats}, ensure_ascii=False, indent=1)
    print(text)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
    return 0 if stats.get("places") else 1


async def main(
    trigger: str,
    out: Path | None,
    ai_review: bool = False,
    ai_tag_limit: int | None = None,
    embed_step: bool = False,
) -> int:
    if names := missing():
        print(f"{', '.join(names)} 값이 없습니다. {ENV_HINT}", file=sys.stderr)
        return 2
    result = await sync_once(trigger, None, ai_review, ai_tag_limit, embed_step)
    status = result["status"]
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
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="동기화 한 번 실행")
    mode.add_argument(
        "--watch", action="store_true", help="관리자 요청·정기 동기화를 기다리며 계속 실행"
    )
    mode.add_argument(
        "--embed-only", action="store_true", help="동기화 없이 사전 임베딩만 채움(FR-617)"
    )
    mode.add_argument(
        "--tags-only", action="store_true", help="동기화 없이 규칙 태그만 붙임(FR-607)"
    )
    mode.add_argument(
        "--photos-only", action="store_true", help="동기화 없이 장소 사진만 다시 붙임(FR-207)"
    )
    p.add_argument("--trigger", choices=["manual", "schedule"], default="manual")
    p.add_argument("--env-file", type=Path, help="KEY=값 파일. 없으면 저장소 루트 .env")
    p.add_argument("--out", type=Path, help="결과 JSON을 UTF-8 파일로도 저장")
    p.add_argument("--ai-review", action="store_true", help="반영 뒤 AI 제안 단계(FR-608)도 실행")
    p.add_argument("--ai-tag-limit", type=int, help="AI에 태그를 물을 촬영지 수(없으면 전부)")
    p.add_argument("--embed", action="store_true", help="반영 뒤 사전 임베딩 단계(FR-617)도 실행")
    a = p.parse_args(argv)
    if a.env_file and not a.env_file.is_file():
        print(f"{a.env_file} 파일이 없습니다. {ENV_HINT}", file=sys.stderr)
        return 2
    if (env_file := a.env_file or ROOT_ENV).is_file():
        load_env_file(env_file)
    setup_logging(_env(os.environ, "LOG_LEVEL") or "INFO")
    if a.watch:
        try:
            return asyncio.run(watch(a.ai_review, a.ai_tag_limit, a.embed))
        except KeyboardInterrupt:
            return 0
    if a.embed_only:
        return asyncio.run(embed_only(a.out))
    if a.tags_only:
        return asyncio.run(tags_only(a.out))
    if a.photos_only:
        return asyncio.run(photos_only(a.out))
    return asyncio.run(main(a.trigger, a.out, a.ai_review, a.ai_tag_limit, a.embed))


if __name__ == "__main__":
    sys.exit(cli())
