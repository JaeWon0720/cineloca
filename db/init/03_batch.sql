-- C1 배치 스테이징과 반영 키 (ADR-012, FR-601~607·611)
--
-- 배치는 수집·병합·매칭·좌표 결과를 stg 스키마에 먼저 쌓고, 건수 가드(FR-612)를 통과하면
-- 한 트랜잭션에서 서비스 테이블로 반영한다. 서비스 테이블의 id는 정규화한 이름으로 찾아 유지한다
-- (두 번 돌려도 작품·촬영지 id가 바뀌지 않아야 검수 결과·코스가 깨지지 않는다).

CREATE SCHEMA IF NOT EXISTS stg;

-- 반영 키: 같은 정규화 이름은 하나만 둔다(PoC p2의 병합 기준과 같다)
CREATE UNIQUE INDEX IF NOT EXISTS work_title_norm_key     ON work (title_norm);
CREATE UNIQUE INDEX IF NOT EXISTS location_name_norm_key  ON location (name_norm);

CREATE TABLE IF NOT EXISTS stg.work (
    title_norm   TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    release_year INTEGER,
    kmdb_id      TEXT,
    kmdb         JSONB,
    match_status TEXT NOT NULL,
    match_score  NUMERIC(4, 3),
    match_rule   TEXT,                 -- score | rule_c | rule_u | review(검수 확정) | tie | none
    candidate    JSONB,                -- 검수 대기일 때 제안(최고 후보)
    source_id    VARCHAR(10) NOT NULL
);

CREATE TABLE IF NOT EXISTS stg.location (
    name_norm          TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    address            TEXT,
    gu                 TEXT,
    lat                DOUBLE PRECISION,
    lon                DOUBLE PRECISION,
    geo_via            TEXT,           -- address | name | cache | none
    nearest_station_id VARCHAR(10),
    station_walk_min   NUMERIC(6, 2)
);

CREATE TABLE IF NOT EXISTS stg.shooting (
    title_norm TEXT NOT NULL,
    name_norm  TEXT NOT NULL,
    source_id  VARCHAR(10) NOT NULL,
    raw        JSONB,
    base_date  DATE,
    PRIMARY KEY (title_norm, name_norm, source_id)
);

CREATE TABLE IF NOT EXISTS stg.station (
    id   VARCHAR(10) PRIMARY KEY,
    name TEXT NOT NULL,
    line TEXT NOT NULL,
    lat  DOUBLE PRECISION NOT NULL,
    lon  DOUBLE PRECISION NOT NULL,
    gu   TEXT
);

-- 출처 대장 (FR-611, BR-02). 이용조건은 2026-11-11 각 기관 페이지 기준
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-01', '부산광역시(부산영상위원회)', '영화 속 부산정보', 'https://www.data.go.kr/data/15164328/openapi.do', '공공데이터 이용허락범위 제한 없음', 'STORE'),
 ('DATA-02', '부산광역시(부산영상위원회)', '씨네맵', 'https://www.data.go.kr/data/15164338/openapi.do', '공공데이터 이용허락범위 제한 없음', 'STORE'),
 ('DATA-04', '한국영상자료원', 'KMDb 영화정보', 'https://www.kmdb.or.kr/info/api/apiDetail/6', '공공누리 제1유형(출처표시). 포스터·스틸은 기관 외 저작물', 'STORE'),
 ('DATA-06', '행정안전부', '도로명주소 검색·좌표제공 API', 'https://business.juso.go.kr', '주소기반산업지원서비스 이용약관', 'STORE'),
 ('DATA-08', '부산교통공사', '도시철도 역사 정보', 'https://www.data.go.kr/data/15158991/openapi.do', '공공데이터 이용허락범위 제한 없음', 'STORE')
ON CONFLICT (id) DO NOTHING;
