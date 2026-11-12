"""저장 정책 감사 (FR-703, NFR-003, NFR-007, BR-01, AC-15).

DB 스키마(db/init/*.sql)에 저장하면 안 되는 데이터를 담을 테이블·열이 없는지 PR마다 CI에서 검사한다.
- NO_STORE: 카카오 응답, 영화 이미지 파일 (BR-01)
- 사용자 위치 좌표, 마스킹 전 입력 원문 (NFR-003)
- 서버에 두지 않는 사용자 기록: 스탬프·배지·저장 코스·질의 세션 (ADR-009, ADR-013)
적재 데이터 감사는 C1 시드가 생기면 추가한다.
"""

import re
from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "db" / "init"

FORBIDDEN_TABLE = re.compile(r"kakao|stamp|badge|session|query_log|chat|visit|user_course|saved_")
FORBIDDEN_COLUMN = re.compile(
    r"kakao"
    r"|^(user|current|my|client)_(lat|lon|lng|latitude|longitude|location|position|coords?)$"
    r"|^(raw_query|query_text|input_text|raw_input|prompt|user_input|original_text)$"
    r"|^(image|poster|still|thumbnail|photo)_(data|file|blob|bytes)$"
)
BINARY_TYPES = {"bytea", "blob", "oid"}
NON_COLUMN_WORDS = {"constraint", "primary", "foreign", "unique", "check", "exclude"}


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def parse_tables(sql: str) -> dict[str, list[tuple[str, str]]]:
    """CREATE TABLE마다 (열 이름, 자료형) 목록을 돌려준다."""
    tables: dict[str, list[tuple[str, str]]] = {}
    body_pattern = r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?([\w.]+)\s*\((.*?)\n\s*\);"
    for match in re.finditer(body_pattern, _strip_comments(sql), re.S | re.I):
        name, body = match.group(1).lower(), match.group(2)
        columns = []
        for line in body.splitlines():
            tokens = line.strip().rstrip(",").split()
            if len(tokens) < 2 or tokens[0].lower() in NON_COLUMN_WORDS:
                continue
            columns.append((tokens[0].lower(), tokens[1].lower()))
        tables[name] = columns
    return tables


def _load_schema() -> dict[str, list[tuple[str, str]]]:
    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(SCHEMA_DIR.glob("*.sql")))
    return parse_tables(sql)


@pytest.fixture(scope="module")
def schema() -> dict[str, list[tuple[str, str]]]:
    tables = _load_schema()
    assert tables, f"{SCHEMA_DIR}에서 CREATE TABLE을 찾지 못했습니다"
    return tables


def test_no_forbidden_tables(schema: dict[str, list[tuple[str, str]]]) -> None:
    bad = [name for name in schema if FORBIDDEN_TABLE.search(name)]
    assert bad == [], f"서버에 두면 안 되는 데이터를 담는 테이블: {bad}"


def test_no_forbidden_columns(schema: dict[str, list[tuple[str, str]]]) -> None:
    bad = [
        f"{table}.{column}"
        for table, columns in schema.items()
        for column, _ in columns
        if FORBIDDEN_COLUMN.search(column)
    ]
    assert bad == [], f"저장 금지 데이터(카카오 응답·사용자 위치·입력 원문·이미지 파일) 열: {bad}"


def test_no_binary_columns(schema: dict[str, list[tuple[str, str]]]) -> None:
    bad = [
        f"{table}.{column} {col_type}"
        for table, columns in schema.items()
        for column, col_type in columns
        if col_type.split("(")[0] in BINARY_TYPES
    ]
    assert bad == [], f"이미지 파일 등 바이너리 저장 열(BR-03: 이미지는 주소만 저장): {bad}"


def test_photo_stores_url_only(schema: dict[str, list[tuple[str, str]]]) -> None:
    columns = dict(schema["photo"])
    assert columns.get("url") == "text"


def test_parser_detects_violations() -> None:
    """감사 규칙 자체가 동작하는지 확인한다(가짜 스키마)."""
    sql = """
    CREATE TABLE kakao_place (
        id SERIAL PRIMARY KEY,
        user_lat DOUBLE PRECISION,
        raw_query TEXT,
        poster_file BYTEA,
        CONSTRAINT x CHECK (id > 0)
    );
    """
    tables = parse_tables(sql)

    assert list(tables) == ["kakao_place"]
    assert [c for c, _ in tables["kakao_place"]] == ["id", "user_lat", "raw_query", "poster_file"]
    assert FORBIDDEN_TABLE.search("kakao_place")
    assert all(FORBIDDEN_COLUMN.search(c) for c in ["user_lat", "raw_query", "poster_file"])
