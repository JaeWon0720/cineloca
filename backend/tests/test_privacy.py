"""COMP-10 Privacy Guard 테스트 (FR-702, NFR-003, NFR-005, AC-14)."""

import io
import logging

import pytest

from app.support.privacy import (
    MASK_NOTICE,
    PLACEHOLDER,
    REMOVED,
    PrivacyLogFilter,
    install_privacy_log_filter,
    mask,
    scrub,
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
        ("abc.def+x@gmail.com", "email"),
        ("900101-1234567", "rrn"),
        ("9001011234567", "rrn"),
        ("1234-5678-9012-3456", "card"),
        ("1234 5678 9012 3456", "card"),
        ("1234567890123456", "card"),
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
