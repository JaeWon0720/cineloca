-- 씨네로케 DB 확장 (ADR-008, ADR-011)
-- pgvector: 임베딩 검색 / pg_trgm: 퍼지 검색 / cube + earthdistance: 반경 검색 (PostGIS는 GPL이라 사용하지 않음)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS cube;
CREATE EXTENSION IF NOT EXISTS earthdistance;
