-- 역간 소요시간 스테이징과 출처 (FR-313, BR-12, DATA-09)
--
-- 배치는 DATA-09(이웃 역 구간)로 모든 역 쌍의 최소 소요시간(분)을 계산해 stg.station_time에 쌓고,
-- 반영 때 station_time을 통째로 바꾼다(이번에 받지 못하면 지난 값 유지).
-- 이미 만든 DB에는 이 파일을 한 번 실행하면 된다(docker compose exec -T db psql … < 이 파일).

CREATE TABLE IF NOT EXISTS stg.station_time (
    from_id VARCHAR(10) NOT NULL,
    to_id   VARCHAR(10) NOT NULL,
    minutes NUMERIC(6, 2) NOT NULL,
    PRIMARY KEY (from_id, to_id)
);

-- 이용조건은 2026-11-18 공공데이터포털 페이지 기준
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-09', '부산교통공사', '역간 거리 및 소요시간 정보', 'https://www.data.go.kr/data/15158989/openapi.do', '공공데이터 이용허락범위 제한 없음', 'STORE')
ON CONFLICT (id) DO NOTHING;
