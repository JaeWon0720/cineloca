-- 씨네로케 지식베이스 스키마 v0 (SDD 3.5 ERD-01, PostgreSQL 16)
--
-- 저장하지 않는 데이터(NFR-003, NFR-007, BR-01): 사용자 위치, 입력 원문, 카카오 응답, 영화 이미지 파일,
-- 스탬프·배지·저장 코스·질의 세션(ADR-009, ADR-013). 이 스키마에는 그런 열이 없어야 한다.
-- v0에서 정하지 않은 것: 배치 스테이징 스키마(stg, ADR-012)는 C1 배치 작업에서 추가한다.

-- ─────────────────────────────────────────────
-- 출처 대장 (FR-611, BR-02)
-- ─────────────────────────────────────────────
CREATE TABLE source (
    id           VARCHAR(10) PRIMARY KEY,          -- 예: DATA-01
    org          TEXT        NOT NULL,             -- 제공기관
    name         TEXT        NOT NULL,             -- 데이터명
    url          TEXT        NOT NULL,
    license      TEXT        NOT NULL,             -- 이용조건
    policy       TEXT        NOT NULL CHECK (policy IN ('STORE', 'ID_ONLY', 'NO_STORE')),
    base_date    DATE,                             -- 기준일
    collected_at TIMESTAMPTZ                       -- 수집일
);

-- ─────────────────────────────────────────────
-- 도시철도역·역간 소요시간 (FR-606, FR-313, BR-12)
-- ─────────────────────────────────────────────
CREATE TABLE station (
    id   VARCHAR(10)      PRIMARY KEY,             -- 역코드
    name TEXT             NOT NULL,
    line TEXT             NOT NULL,                -- 호선
    lat  DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon  DOUBLE PRECISION NOT NULL CHECK (lon BETWEEN -180 AND 180),
    gu   TEXT
);

CREATE TABLE station_time (
    from_id VARCHAR(10) NOT NULL REFERENCES station (id),
    to_id   VARCHAR(10) NOT NULL REFERENCES station (id),
    minutes NUMERIC(6, 2) NOT NULL CHECK (minutes >= 0),
    PRIMARY KEY (from_id, to_id)
);

-- ─────────────────────────────────────────────
-- 작품·인물 (FR-101~107, FR-603·604)
-- ─────────────────────────────────────────────
CREATE TABLE work (
    id           SERIAL PRIMARY KEY,
    title        TEXT    NOT NULL,
    title_norm   TEXT    NOT NULL,                 -- 정규화한 제목(FR-602)
    release_year INTEGER CHECK (release_year BETWEEN 1900 AND 2100),
    genres       TEXT[]  NOT NULL DEFAULT '{}',
    kmdb_id      TEXT UNIQUE,                      -- 미매칭 작품은 NULL(FR-604)
    kmdb         JSONB,                            -- KMDb 저장 정책이 STORE일 때만 채움(ADR-007, TBD-01)
    match_status TEXT    NOT NULL DEFAULT 'unmatched'
                 CHECK (match_status IN ('matched', 'review', 'unmatched')),  -- BR-11
    match_score  NUMERIC(4, 3) CHECK (match_score BETWEEN 0 AND 1),
    source_id    VARCHAR(10) NOT NULL REFERENCES source (id)
);

CREATE TABLE person (
    id             SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    kmdb_person_id TEXT UNIQUE
);

CREATE TABLE work_person (
    work_id   INTEGER NOT NULL REFERENCES work (id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES person (id) ON DELETE CASCADE,
    role      TEXT    NOT NULL CHECK (role IN ('director', 'actor')),
    ord       SMALLINT NOT NULL DEFAULT 0,          -- 출연 순서(FR-103: 최대 5명 표시)
    PRIMARY KEY (work_id, person_id, role)
);

-- ─────────────────────────────────────────────
-- 촬영지 (FR-201~210, FR-605~610, BR-06·07)
-- ─────────────────────────────────────────────
CREATE TABLE location (
    id                 SERIAL PRIMARY KEY,
    name               TEXT NOT NULL,
    name_norm          TEXT NOT NULL,
    address            TEXT,
    gu                 TEXT,                       -- 구·군(BR-15)
    lat                DOUBLE PRECISION CHECK (lat BETWEEN -90 AND 90),     -- 좌표 없으면 NULL(FR-210)
    lon                DOUBLE PRECISION CHECK (lon BETWEEN -180 AND 180),
    tags               TEXT[]  NOT NULL DEFAULT '{}',   -- 승인된 태그만(BR-04)
    caution            TEXT CHECK (caution IN ('private', 'residential', 'school')),  -- FR-209
    safety_note        TEXT,
    cert_radius_m      INTEGER NOT NULL DEFAULT 100 CHECK (cert_radius_m BETWEEN 50 AND 300),  -- BR-07
    cert_excluded      BOOLEAN NOT NULL DEFAULT FALSE,  -- BR-06
    nearest_station_id VARCHAR(10) REFERENCES station (id),
    station_walk_min   NUMERIC(6, 2) CHECK (station_walk_min >= 0),
    embedding          VECTOR(1024),               -- bge-m3(FR-617)
    CONSTRAINT location_latlon_pair CHECK ((lat IS NULL) = (lon IS NULL))
);

-- 촬영 기록: '작품 A가 촬영지 B에서 촬영됨' (FR-602)
CREATE TABLE shooting (
    work_id     INTEGER     NOT NULL REFERENCES work (id) ON DELETE CASCADE,
    location_id INTEGER     NOT NULL REFERENCES location (id) ON DELETE CASCADE,
    source_id   VARCHAR(10) NOT NULL REFERENCES source (id),
    raw         JSONB,                             -- 원본 레코드(STORE 데이터만)
    base_date   DATE,
    PRIMARY KEY (work_id, location_id, source_id)
);

-- 장소 사진: 이미지 주소만 저장, 파일은 저장하지 않음 (FR-207, BR-03)
CREATE TABLE photo (
    id          SERIAL PRIMARY KEY,
    location_id INTEGER     NOT NULL REFERENCES location (id) ON DELETE CASCADE,
    url         TEXT        NOT NULL,
    kogl_type   SMALLINT    NOT NULL CHECK (kogl_type = 1),  -- 공공누리 제1유형만
    title       TEXT,
    source_id   VARCHAR(10) NOT NULL REFERENCES source (id)
);

-- ─────────────────────────────────────────────
-- 추천 코스 (FR-301·302, FR-614, BR-13)
-- 사용자가 만든 코스는 브라우저에만 저장하므로 테이블이 없다(FR-306).
-- ─────────────────────────────────────────────
CREATE TABLE course (
    id                SERIAL PRIMARY KEY,
    theme             TEXT NOT NULL CHECK (theme IN ('work', 'person', 'district')),  -- 작품·배우·지역
    theme_ref         TEXT,                        -- 테마 대상 ID 또는 구·군 이름
    title             TEXT NOT NULL,
    start_station_id  VARCHAR(10) REFERENCES station (id),   -- v0 가정: 출발지는 역 또는 촬영지
    start_location_id INTEGER REFERENCES location (id),
    total_min         INTEGER CHECK (total_min >= 0),
    total_m           INTEGER CHECK (total_m >= 0),
    status            TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved')),
    approved_by       TEXT,
    approved_at       TIMESTAMPTZ,
    CONSTRAINT course_single_start CHECK (start_station_id IS NULL OR start_location_id IS NULL)
);

CREATE TABLE course_stop (
    course_id   INTEGER  NOT NULL REFERENCES course (id) ON DELETE CASCADE,
    ord         SMALLINT NOT NULL CHECK (ord >= 1),
    location_id INTEGER  NOT NULL REFERENCES location (id),
    dwell_min   SMALLINT NOT NULL DEFAULT 20 CHECK (dwell_min >= 0),   -- BR-13
    leg_mode    TEXT CHECK (leg_mode IN ('walk', 'subway')),           -- 직전 장소에서 오는 이동 수단
    leg_min     NUMERIC(6, 2) CHECK (leg_min >= 0),
    leg_line    TEXT,                                                  -- 도시철도 노선
    PRIMARY KEY (course_id, ord)
);

-- ─────────────────────────────────────────────
-- 검수 큐 (FR-608·609, BR-04)
-- ─────────────────────────────────────────────
CREATE TABLE review_item (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('tag', 'match')),
    target_type TEXT NOT NULL CHECK (target_type IN ('location', 'work')),
    target_id   INTEGER NOT NULL,
    proposal    JSONB NOT NULL,
    proposer    TEXT NOT NULL CHECK (proposer IN ('rule', 'ai')),
    score       NUMERIC(4, 3),
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'modified', 'rejected')),
    handled_by  TEXT,
    handled_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────
-- 운영·보조 테이블 (외래 키 없음)
-- ─────────────────────────────────────────────
-- 사전 대조용 사전 (FR-403, FR-617)
CREATE TABLE dict_entry (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('work', 'person', 'district', 'station')),
    surface      TEXT NOT NULL,                    -- 표기(별칭 포함)
    canonical_id TEXT NOT NULL,                    -- 표준 값의 ID
    embedding    VECTOR(1024)
);

-- 동기화 기록 (FR-612·613, ADR-012)
CREATE TABLE sync_run (
    id          BIGSERIAL PRIMARY KEY,
    trigger     TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual')),
    status      TEXT NOT NULL DEFAULT 'requested'
                CHECK (status IN ('requested', 'running', 'succeeded', 'failed', 'guarded')),
    counts      JSONB,
    prev_counts JSONB,
    failures    JSONB,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- AI 응답 캐시 24시간 (AI-REQ-15, ADR-013)
CREATE TABLE ai_cache (
    key        CHAR(64)    PRIMARY KEY,            -- 질의·근거의 해시
    task       TEXT        NOT NULL,
    response   JSONB       NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

-- 외부 호출 수 (NFR-005, NFR-018)
CREATE TABLE usage_counter (
    day   DATE    NOT NULL,
    key   TEXT    NOT NULL,                        -- 예: gemini, DATA-01
    count INTEGER NOT NULL DEFAULT 0 CHECK (count >= 0),
    PRIMARY KEY (day, key)
);

-- ─────────────────────────────────────────────
-- 색인 (SDD 3.5)
-- ─────────────────────────────────────────────
CREATE INDEX location_geo_idx       ON location USING gist (ll_to_earth(lat, lon)) WHERE lat IS NOT NULL;
CREATE INDEX location_tags_idx      ON location USING gin (tags);
CREATE INDEX location_name_trgm_idx ON location USING gin (name_norm gin_trgm_ops);
CREATE INDEX location_embedding_idx ON location USING hnsw (embedding vector_cosine_ops);

CREATE INDEX work_title_trgm_idx ON work USING gin (title_norm gin_trgm_ops);
CREATE INDEX work_genres_idx     ON work USING gin (genres);

CREATE INDEX dict_entry_surface_trgm_idx ON dict_entry USING gin (surface gin_trgm_ops);
CREATE INDEX dict_entry_embedding_idx    ON dict_entry USING hnsw (embedding vector_cosine_ops);

CREATE INDEX ai_cache_expires_idx ON ai_cache (expires_at);

-- 조회용 보조 색인
CREATE INDEX shooting_location_idx    ON shooting (location_id);
CREATE INDEX photo_location_idx       ON photo (location_id);
CREATE INDEX review_item_pending_idx  ON review_item (status, kind) WHERE status = 'pending';
