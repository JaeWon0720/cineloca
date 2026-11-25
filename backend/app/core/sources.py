"""출처 대장에 없는 출처와 라이선스 목록 (UI-09 데이터 출처 화면, FR-705, API-019).

source 테이블은 저장하는 데이터(STORE)의 출처 대장이다. 저장하지 않고 화면·요청 때만 쓰는
외부 서비스(NO_STORE)는 테이블에 행이 없어서 여기 적어 두고 대장 뒤에 붙인다.
라이선스는 서비스가 쓰는 오픈소스·AI 모델이다. 의존성을 바꾸면 이 목록도 고친다.
"""

from typing import Literal

Policy = Literal["STORE", "ID_ONLY", "NO_STORE"]

RUNTIME_SOURCES: tuple[dict[str, str | None], ...] = (
    {
        "id": "DATA-07",
        "provider": "카카오",
        "name": "카카오 지도 JavaScript SDK",
        "url": "https://apis.map.kakao.com",
        "base_date": None,
        "license": "카카오 지도 API 이용약관(응답·좌표 저장 금지)",
        "policy": "NO_STORE",
    },
    {
        "id": "DATA-13",
        "provider": "한국관광공사",
        "name": "TourAPI 위치기반 관광정보(음식점·숙박)",
        "url": "https://api.visitkorea.or.kr",
        "base_date": None,
        "license": "TourAPI 이용약관(요청 때만 조회, 응답 저장 안 함)",
        "policy": "NO_STORE",
    },
)

LICENSES: tuple[tuple[str, str], ...] = (
    ("FastAPI · Pydantic · PyJWT · RapidFuzz · pyproj", "MIT"),
    ("Uvicorn · HTTPX", "BSD-3-Clause"),
    ("asyncpg", "Apache-2.0"),
    ("PostgreSQL · pgvector", "PostgreSQL License"),
    ("React · React Router · Vite", "MIT"),
    ("Leaflet (지도 대체 표시)", "BSD-2-Clause"),
    ("OpenStreetMap 지도 타일 (대체 표시)", "ODbL — © OpenStreetMap contributors"),
    ("IBM Plex Sans KR · Black Han Sans 글꼴", "SIL Open Font License 1.1"),
    ("Ollama", "MIT"),
    ("Qwen3 (로컬 언어 모델)", "Apache-2.0"),
    ("BGE-M3 (임베딩 모델)", "MIT"),
    ("Google Gemini API (선택 공급자)", "Gemini API 이용약관"),
)
