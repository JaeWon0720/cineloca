# 씨네로케 DB (COMP-12 Knowledge Base)

PostgreSQL 16 + pgvector. 스키마는 SDD 3.5 ERD-01을 따른다.

| 파일 | 내용 |
|---|---|
| `init/01_extensions.sql` | 확장: vector, pg_trgm, cube, earthdistance (PostGIS는 GPL이라 사용하지 않음, ADR-011) |
| `init/02_schema.sql` | 테이블 16개와 색인 (스키마 v0) |
| `init/03_batch.sql` | 배치 스테이징 스키마 `stg` (ADR-012) |
| `init/05_station_time.sql` | 역간 소요시간 표 (FR-413) |

`init/` 아래 파일은 파일 이름 순서대로 실행한다. Docker Compose의 db 컨테이너가 **처음** 시작할 때(DB 볼륨이 비어 있을 때) 자동으로 실행한다. 표만 만들고 데이터는 넣지 않는다.

## 시드 넣기 (실제 데이터)

작품·촬영지·좌표·역 데이터는 저장소에 넣지 않고 [Releases](https://github.com/JaeWon0720/cineloca/releases)의 `seed-…` 판으로 배포한다(build-plan '시드 데이터'). 시드를 넣지 않으면 화면이 실제 API에 붙어도 목록이 비어 있다. 배치(`docker compose --profile batch`)를 키와 함께 돌려도 같은 데이터가 생기지만, 공공데이터 API 키와 수집 시간이 필요하다.

1. 최신 `seed-…` Release에서 `seed.sql.gz`와 `seed.sha256.txt`를 받는다.
   - 받은 파일 확인(PowerShell): `(Get-FileHash seed.sql.gz).Hash` 값이 `seed.sha256.txt`의 앞 64자와 같아야 한다(대소문자 무시).
2. **빈 DB**에서 시작한다. 시드는 id까지 들어 있어 데이터가 있는 DB에 넣으면 중복 오류가 난다.
   ```powershell
   docker compose down -v           # 주의: DB 볼륨(기존 데이터)을 지운다
   docker compose up -d db          # db/init이 표를 만든다
   ```
3. 시드를 넣는다(Windows·macOS·Linux 같음, 압축은 컨테이너 안에서 푼다).
   ```powershell
   docker compose cp seed.sql.gz db:/tmp/seed.sql.gz
   docker compose exec db sh -c 'gunzip -c /tmp/seed.sql.gz | psql -q -v ON_ERROR_STOP=1 -U $POSTGRES_USER -d $POSTGRES_DB'
   ```
4. 확인: `docker compose exec db psql -U cineloca -d cineloca -c "SELECT count(*) FROM work"` → Release 설명의 작품 수와 같으면 된다(`.env`에서 DB 이름·사용자를 바꿨으면 그 값으로).
5. 나머지를 띄운다: `docker compose up -d --build`

시드에 없는 것과 직접 할 일:
- 추천 코스: 관리자가 후보를 만들어 승인해야 사용자 화면에 보인다(API-024, FR-614). 관리자 기능은 `.env`의 `ADMIN_PASSWORD_HASH`가 있어야 열린다.
- 검수 대기: KMDb 연결이 애매한 작품은 검수 큐(`review_item`)에 들어 있다. 관리자 검수 화면에서 처리하면 다음 동기화 때 반영된다(FR-609·611).
- 임베딩(비슷한 이름 찾기, FR-617): `seed-2026-11-25b`부터 사전(dict_entry) 2,828개에 모두 들어 있다(bge-m3, 1024차원).
  질문 쪽 임베딩은 로컬 모델(`OLLAMA_BASE_URL`)이 있을 때만 만들어서, 없는 PC에서는 지금처럼 이름 비교(정확 일치·트라이그램)까지만 쓴다.
  사전이 바뀐 DB에 다시 채우려면 `python -m app.batch --embed-only`(로컬 모델 필요).
- 장소 유형 태그(FR-607)와 관람 주의 구분(FR-209, BR-06): `seed-2026-11-27`부터 들어 있다(태그 868곳, 관람 주의 204곳 중 좌표 있는 곳 학교 39·주거지 91·사유지 10, #148·#153).
  관람 주의가 붙은 곳은 상세에 '외부에서만 관람' 안내가 나오고 방문 인증에서 빠진다.
  `seed-2026-11-25b`까지를 넣은 DB는 새로 넣지 않고 `docker compose exec api python -m app.batch --tags-only` 한 번이면 같은 결과다
  (외부 API를 부르지 않음, 빈 곳에만 붙여 여러 번 돌려도 같고 관리자가 정한 값은 그대로 둔다). 다음 동기화부터는 반영할 때 자동으로 붙는다.

## 저장하지 않는 데이터 (NFR-003, NFR-007)

사용자 위치, 입력 원문, 카카오 응답, 영화 이미지 파일, 스탬프·배지·저장 코스·질의 세션은 어떤 테이블에도 두지 않는다. 열을 추가할 때 이 규칙을 지키는지 PR에서 확인한다.

## v0에서 남겨 둔 것

- 배치 스테이징 스키마 `stg` (ADR-012): C1 배치 작업에서 추가
- `course`의 출발지 표현(역 또는 촬영지): ERD의 `start_*`를 v0 가정으로 구체화. 코스 기능 구현 때 확정
- 열 이름·형식은 착수 PoC(TBD-02~04) 결과에 따라 바뀔 수 있음
