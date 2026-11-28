-- 장소 사진 출처 (FR-207, BR-03). 이용조건은 2026-11-28 공공데이터포털 페이지 기준
-- 사진 파일은 저장하지 않고 주소만 photo.url에 둔다. 공공누리 제1유형만 쓴다(photo.kogl_type CHECK = 1)
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-10', '한국관광공사', 'TourAPI 관광지 대표 이미지', 'https://www.data.go.kr/data/15101578/openapi.do', '공공누리 제1유형(출처표시)', 'STORE')
ON CONFLICT (id) DO NOTHING;
