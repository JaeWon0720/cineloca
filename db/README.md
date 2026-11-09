# 씨네로케 DB (COMP-12 Knowledge Base)

PostgreSQL 16 + pgvector. 스키마는 SDD 3.5 ERD-01을 따른다.

| 파일 | 내용 |
|---|---|
| `init/01_extensions.sql` | 확장: vector, pg_trgm, cube, earthdistance (PostGIS는 GPL이라 사용하지 않음, ADR-011) |
| `init/02_schema.sql` | 테이블 16개와 색인 (스키마 v0) |

`init/` 아래 파일은 파일 이름 순서대로 실행한다. Phase 3에서 Docker Compose의 db 컨테이너가 처음 시작할 때 자동으로 실행하도록 연결한다.

## 저장하지 않는 데이터 (NFR-003, NFR-007)

사용자 위치, 입력 원문, 카카오 응답, 영화 이미지 파일, 스탬프·배지·저장 코스·질의 세션은 어떤 테이블에도 두지 않는다. 열을 추가할 때 이 규칙을 지키는지 PR에서 확인한다.

## v0에서 남겨 둔 것

- 배치 스테이징 스키마 `stg` (ADR-012): C1 배치 작업에서 추가
- `course`의 출발지 표현(역 또는 촬영지): ERD의 `start_*`를 v0 가정으로 구체화. 코스 기능 구현 때 확정
- 열 이름·형식은 착수 PoC(TBD-02~04) 결과에 따라 바뀔 수 있음
