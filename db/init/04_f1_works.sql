-- F1 작품 API (API-001~003) 보충: KOBIS 영화 코드 열과 KOBIS 출처 (SDD 3.5 work.kobis_movie_cd, FR-107)
--
-- KOBIS는 실시간 사용만 허용한다(ADR-007). 영화 코드만 저장하고(ID_ONLY) 응답 본문은 어디에도 두지 않는다.
-- 코드는 아직 배치가 채우지 않는다. 비어 있으면 API-003은 rating=null을 돌려준다.

ALTER TABLE work ADD COLUMN IF NOT EXISTS kobis_movie_cd VARCHAR(10);

INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-05', '영화진흥위원회', 'KOBIS 영화정보', 'https://www.kobis.or.kr/kobisopenapi', '실시간 사용만 허용, 복제·저장·재전송 금지', 'ID_ONLY')
ON CONFLICT (id) DO NOTHING;
