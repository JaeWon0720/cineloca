"""COMP-10 Privacy Guard 테스트 (FR-702, NFR-003, NFR-005, AC-14)."""

import io
import logging
import logging.config
import sys
from collections.abc import Iterator

import pytest

from app.support.privacy import (
    MASK_NOTICE,
    PLACEHOLDER,
    REMOVED,
    PrivacyLogFilter,
    install_privacy_log_filter,
    mask,
    scrub,
    strip_params,
)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("010-1234-5678", "phone"),
        ("01012345678", "phone"),
        ("010 1234 5678", "phone"),
        ("010.1234.5678", "phone"),
        ("+82 10-1234-5678", "phone"),
        ("+821012345678", "phone"),
        ("051-123-4567", "phone"),
        ("02-1234-5678", "phone"),
        ("070-1234-5678", "phone"),
        ("010 - 1234 - 5678", "phone"),
        ("051 - 123 - 4567", "phone"),
        ("abc.def+x@gmail.com", "email"),
        ("900101-1234567", "rrn"),
        ("9001011234567", "rrn"),
        ("900101 - 1234567", "rrn"),
        ("1234-5678-9012-3456", "card"),
        ("1234 5678 9012 3456", "card"),
        ("1234567890123456", "card"),
        ("1234-5678 9012-3456", "card"),
        ("3782-822463-10005", "card"),
    ],
)
def test_mask_replaces_personal_info(text: str, kind: str) -> None:
    result = mask(f"제 정보는 {text} 입니다")

    assert result.text == f"제 정보는 {PLACEHOLDER} 입니다"
    assert result.kinds == (kind,)
    assert result.masked is True


@pytest.mark.parametrize(
    "text",
    [
        "2016년 개봉한 부산행 촬영지",
        "역에서 걸어서 10분, 500m 이내",
        "1호선 101번 출구 근처",
        "2024-11-12에 방문",
        "해운대 1km 안 촬영지 3곳",
        "부산행(2016) 공유 마동석",
        "1588-1234",
    ],
)
def test_mask_keeps_ordinary_numbers(text: str) -> None:
    result = mask(text)

    assert result.text == text
    assert result.kinds == ()
    assert result.masked is False


def test_mask_reports_each_kind_once_in_order() -> None:
    result = mask("a@b.co 010-1111-2222 c@d.co 010-3333-4444")

    assert result.text == f"{PLACEHOLDER} {PLACEHOLDER} {PLACEHOLDER} {PLACEHOLDER}"
    assert result.kinds == ("email", "phone")


def test_notice_message() -> None:
    assert MASK_NOTICE == "개인정보로 보이는 내용을 가렸습니다"


def test_scrub_removes_location_and_secrets_and_masks_strings() -> None:
    record = {
        "path": "/api/ask",
        "query": "제 번호 010-1234-5678로 연락",
        "lat": 35.1587,
        "LON": 129.1604,
        "nested": {"longitude": 129.0, "Password": "pw", "serviceKey": "abc"},
        "items": ["a@b.co", 3, None],
        "pair": ("010-1111-2222", 1),
        "status": 200,
    }

    cleaned = scrub(record)

    assert cleaned == {
        "path": "/api/ask",
        "query": f"제 번호 {PLACEHOLDER}로 연락",
        "lat": REMOVED,
        "LON": REMOVED,
        "nested": {"longitude": REMOVED, "Password": REMOVED, "serviceKey": REMOVED},
        "items": [PLACEHOLDER, 3, None],
        "pair": (PLACEHOLDER, 1),
        "status": 200,
    }


def test_scrub_does_not_change_original() -> None:
    record = {"lat": 35.1, "query": "010-1234-5678"}

    scrub(record)

    assert record == {"lat": 35.1, "query": "010-1234-5678"}


@pytest.mark.parametrize(
    "key",
    [
        "GEMINI_API_KEY",
        "KMDB_SERVICE_KEY",
        "DATA_GO_KR_SERVICE_KEY",
        "JUSO_COORD_API_KEY",
        "access_token",
        "refreshToken",
        "apikey",
        "client_secret",
        "Set-Cookie",
        "user_lat",
        "userLng",
        "current_longitude",
        "mapX",
        "mapy",
        "x",
        "geo_coords",
    ],
)
def test_scrub_removes_real_key_names(key: str) -> None:
    assert scrub({key: "value"}) == {key: REMOVED}


@pytest.mark.parametrize(
    "key", ["latency_ms", "template", "translation", "platform", "long_text", "status", "path"]
)
def test_scrub_keeps_similar_but_safe_key_names(key: str) -> None:
    assert scrub({key: 12}) == {key: 12}


def _capture_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, stream


def test_log_filter_masks_message_and_args() -> None:
    logger, stream = _capture_logger("test.privacy.filter")
    logger.handlers[0].addFilter(PrivacyLogFilter())

    logger.info("직접 010-1234-5678")
    logger.info("인자 %s / %s", "a@b.co", 7)
    logger.info("이름 인자 %(q)s", {"q": "900101-1234567"})

    lines = stream.getvalue().splitlines()
    assert lines == [f"직접 {PLACEHOLDER}", f"인자 {PLACEHOLDER} / 7", f"이름 인자 {PLACEHOLDER}"]


def test_install_privacy_log_filter_is_idempotent() -> None:
    logger, stream = _capture_logger("test.privacy.install")

    install_privacy_log_filter(["test.privacy.install"])
    install_privacy_log_filter(["test.privacy.install"])
    logger.info("문의 010-9999-8888")

    filters = [f for f in logger.handlers[0].filters if isinstance(f, PrivacyLogFilter)]
    assert len(filters) == 1
    assert stream.getvalue().strip() == f"문의 {PLACEHOLDER}"


@pytest.fixture
def isolated_logging() -> Iterator[None]:
    """root·uvicorn 로거 상태를 저장했다가 되돌린다(pytest의 로그 수집 핸들러도 잠시 뺀다)."""
    names = ["", "uvicorn", "uvicorn.error", "uvicorn.access", "app.test"]
    saved = {}
    for name in names:
        logger = logging.getLogger(name)
        saved[name] = (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
        logger.handlers = []
    yield
    for name, (handlers, level, propagate, disabled) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled


def test_app_logger_is_masked_via_root_handler(
    isolated_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    uvicorn_config = pytest.importorskip("uvicorn.config")
    logging.config.dictConfig(uvicorn_config.LOGGING_CONFIG)  # uvicorn 기본 로그 설정
    # 실제 서버처럼 root에 핸들러가 없는 상태로 만든다(pytest 로그 수집 핸들러 제거)
    logging.getLogger().handlers = []

    install_privacy_log_filter()
    install_privacy_log_filter()  # 두 번 불러도 핸들러는 하나
    app_logger = logging.getLogger("app.test")
    app_logger.propagate = True
    app_logger.warning("앱 로그 010-1234-5678")
    logging.getLogger("uvicorn.error").warning("uvicorn 로그 a@b.co")

    err = capsys.readouterr().err
    assert "010-1234-5678" not in err and "a@b.co" not in err
    assert f"앱 로그 {PLACEHOLDER}" in err
    assert f"uvicorn 로그 {PLACEHOLDER}" in err
    root_handlers = logging.getLogger().handlers
    assert len(root_handlers) == 1
    assert any(isinstance(f, PrivacyLogFilter) for f in root_handlers[0].filters)


def test_access_log_does_not_keep_client_ip(
    isolated_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    uvicorn_config = pytest.importorskip("uvicorn.config")
    logging.config.dictConfig(uvicorn_config.LOGGING_CONFIG)

    install_privacy_log_filter()
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "203.0.113.7:51234", "GET", "/api/health", "1.1", 200
    )

    out = capsys.readouterr().out
    assert "203.0.113.7" not in out
    assert f'{REMOVED} - "GET /api/health HTTP/1.1" 200' in out


def test_access_record_without_uvicorn_formatter() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s"',
        ("10.0.0.1:1", "GET", "/"),
        None,
    )

    PrivacyLogFilter().filter(record)

    assert record.getMessage() == f'{REMOVED} - "GET /"'


def test_access_log_drops_query_string() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:1", "GET", "/api/locations/1?lat=35.15871&token=abc", "1.1", 200),
        None,
    )

    PrivacyLogFilter().filter(record)

    assert record.getMessage() == f'{REMOVED} - "GET /api/locations/1 HTTP/1.1" 200'


def test_log_filter_masks_stack_trace() -> None:
    try:
        raise RuntimeError("연락처 010-9876-5432 처리 실패")
    except RuntimeError:
        record = logging.LogRecord(
            "uvicorn.error",
            logging.ERROR,
            __file__,
            0,
            "Exception in ASGI application",
            (),
            sys.exc_info(),
        )

    PrivacyLogFilter().filter(record)
    text = logging.Formatter().format(record)

    assert "Traceback" in text and "RuntimeError" in text
    assert "010-9876-5432" not in text and PLACEHOLDER in text


# ── URL 속 키·좌표 파라미터 (NFR-009, RK10) ─────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "GET https://apis.data.go.kr/x/getBfc?serviceKey=SECRET123%2B&pageNo=1&numOfRows=1000",
            f"GET https://apis.data.go.kr/x/getBfc?serviceKey={REMOVED}&pageNo=1&numOfRows=1000",
        ),
        (
            "http://api.koreafilm.or.kr/search_json2.jsp?collection=kmdb_new2&ServiceKey=AB12&detail=Y",
            f"http://api.koreafilm.or.kr/search_json2.jsp?collection=kmdb_new2&ServiceKey={REMOVED}&detail=Y",
        ),
        (
            "addrLinkApi.do?confmKey=U01TX0&keyword=해운대",
            f"addrLinkApi.do?confmKey={REMOVED}&keyword=해운대",
        ),
        ("api_key=SECRET999 실패", f"api_key={REMOVED} 실패"),
        (
            "/api/x?lat=35.15871&lon=129.16042&radius=1000",
            f"/api/x?lat={REMOVED}&lon={REMOVED}&radius=1000",
        ),
        ("mapX=129.1&mapY=35.1&x=1&y=2", f"mapX={REMOVED}&mapY={REMOVED}&x={REMOVED}&y={REMOVED}"),
    ],
)
def test_strip_params_removes_keys_and_coordinates(text: str, expected: str) -> None:
    assert strip_params(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "latency_ms=12 template=v3 pageNo=2",  # 'lat'가 들어간 다른 이름은 그대로
        "serviceKey= 비어 있음",  # 값이 없으면 바꿀 것이 없음
        "a = b, 일반 문장",
        "x는 1",
    ],
)
def test_strip_params_keeps_ordinary_text(text: str) -> None:
    assert strip_params(text) == text


def test_scrub_strips_params_in_strings() -> None:
    assert scrub({"url": "/p?token=abc&n=1"}) == {"url": f"/p?token={REMOVED}&n=1"}


def test_log_filter_hides_key_in_httpx_url_object() -> None:
    httpx = pytest.importorskip("httpx")
    url = httpx.URL("https://apis.data.go.kr/x", params={"serviceKey": "SECRETKEY123", "pageNo": 1})
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        0,
        'HTTP Request: %s %s "%s %d %s"',
        ("GET", url, "HTTP/1.1", 200, "OK"),
        None,
    )

    PrivacyLogFilter().filter(record)

    message = record.getMessage()
    assert "SECRETKEY123" not in message
    assert f"serviceKey={REMOVED}&pageNo=1" in message and '"HTTP/1.1 200 OK"' in message


def test_log_filter_strips_params_in_message_and_stack_trace() -> None:
    try:
        raise RuntimeError("GET /x?confmKey=TOPSECRET 실패")
    except RuntimeError:
        record = logging.LogRecord(
            "app.batch", logging.ERROR, __file__, 0, "요청 실패 ServiceKey=ABC", (), sys.exc_info()
        )

    PrivacyLogFilter().filter(record)
    text = logging.Formatter().format(record)

    assert "TOPSECRET" not in text and "ABC" not in text and "Traceback" in text


# ── 세대 단위 상세 주소(#87, 레드팀 R10~R12) ─────────────────
@pytest.mark.parametrize(
    ("text", "masked"),
    [
        (
            "부산광역시 가상구 예시로 123 101동 1001호 근처",
            "부산광역시 가상구 예시로 123 [개인정보] 근처",
        ),
        ("해운대구 가상동 99-9번지 2층에 살아요", "해운대구 가상동 [개인정보] 2층에 살아요"),
        ("예시아파트 3동 304호 근처", "예시아파트 [개인정보] 근처"),
        ("1001 호 앞에서", "[개인정보] 앞에서"),
        ("12번지 골목", "[개인정보] 골목"),
    ],
)
def test_masks_unit_level_address(text: str, masked: str) -> None:
    result = mask(text)

    assert result.text == masked and "address" in result.kinds


@pytest.mark.parametrize(
    "text",
    [
        "2호선 서면역 근처, 1호선 남포역",  # 지하철 노선
        "4호선 5호선",
        "광안해변로 219 근처 촬영지",  # 도로명 번호만은 공공장소 주소일 수 있어 둠
        "우1동 해운대 1호 태풍",  # 행정동, 두 자리 이하 호
        "부산타워 2층 카페",
    ],
)
def test_keeps_search_place_words(text: str) -> None:
    assert mask(text).text == text
