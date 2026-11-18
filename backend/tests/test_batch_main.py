"""C1 배치 진입점 테스트 (#26, #39). 키 값은 가짜이고 실제 API를 부르지 않는다."""

import logging

import httpx

import app.batch.__main__ as batch_main
from app.batch.__main__ import build_clients, cli, load_env_file, missing, setup_logging


def test_load_env_file_reads_values_skips_comments_and_keeps_existing(tmp_path) -> None:
    f = tmp_path / ".env"
    f.write_text(
        "﻿# 주석\nDATABASE_URL=postgresql://a\n"
        "KMDB_SERVICE_KEY=         # 한국영상자료원 KMDb\n"
        "JUSO_BASE_URL='http://x # y'\nDATA_GO_KR_SERVICE_KEY=from-file\n잘못된 줄\n",
        encoding="utf-8",
    )
    env = {"DATA_GO_KR_SERVICE_KEY": "already"}
    load_env_file(f, env)
    assert env == {
        "DATA_GO_KR_SERVICE_KEY": "already",
        "DATABASE_URL": "postgresql://a",
        "KMDB_SERVICE_KEY": "",
        "JUSO_BASE_URL": "http://x # y",
    }
    assert missing(env) == []
    assert missing({"DATABASE_URL": " "}) == ["DATABASE_URL", "DATA_GO_KR_SERVICE_KEY"]


def test_cli_missing_env_points_to_root_env_example(monkeypatch, capsys, tmp_path) -> None:
    for name in ("DATABASE_URL", "DATA_GO_KR_SERVICE_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(batch_main, "ROOT_ENV", tmp_path / "no-root.env")  # 개발자 PC의 .env 무시
    assert cli(["--once"]) == 2
    assert "저장소 루트의 .env.example" in capsys.readouterr().err
    assert cli(["--once", "--env-file", str(tmp_path / "none.env")]) == 2
    assert "none.env 파일이 없습니다" in capsys.readouterr().err


def test_build_clients_warns_names_only(caplog) -> None:
    env = {"DATA_GO_KR_SERVICE_KEY": "D-SECRET", "KAKAO_REST_API_KEY": "K-SECRET"}
    with caplog.at_level(logging.INFO, logger="app.batch"):
        c = build_clients(httpx.AsyncClient(), env)
    assert c.data_go_kr_key == "D-SECRET"
    assert c.kakao is None  # 키가 있어도 GEOCODE_KAKAO_KEYWORD 기본은 끔(ADR-006)
    assert c.juso is None and c.kmdb_source is None
    text = caplog.text
    assert "JUSO_SEARCH_API_KEY" in text and "KMDB_SERVICE_KEY" in text
    assert "GEOCODE_KAKAO_KEYWORD" in text
    assert "SECRET" not in text


def test_build_clients_kakao_only_when_flag_on() -> None:
    http = httpx.AsyncClient()
    on = {"DATA_GO_KR_SERVICE_KEY": "D", "GEOCODE_KAKAO_KEYWORD": "true"}
    assert build_clients(http, {**on, "KAKAO_REST_API_KEY": "K"}).kakao is not None
    assert build_clients(http, on).kakao is None  # 켜도 키가 없으면 없음


def test_setup_logging_hides_keys_and_quiets_httpx(capsys) -> None:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    levels = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    root.handlers = []  # 배치를 처음 실행할 때처럼 핸들러가 없는 상태
    try:
        setup_logging()
        assert all(logging.getLogger(n).level == logging.WARNING for n in levels)
        logging.getLogger("app.batch.collect").warning(
            "GET https://apis.data.go.kr/x?serviceKey=SECRET1&pageNo=1"
        )
        logging.getLogger("httpx").warning("GET https://juso/x?confmKey=SECRET2")
        logging.getLogger("httpx").info("HTTP Request: 이 줄은 남지 않는다")
    finally:
        root.handlers = saved[0]
        root.setLevel(saved[1])
        for n, lv in levels.items():
            logging.getLogger(n).setLevel(lv)
    err = capsys.readouterr().err
    assert "pageNo=1" in err and "confmKey=" in err
    assert "SECRET1" not in err and "SECRET2" not in err
    assert "이 줄은 남지 않는다" not in err
