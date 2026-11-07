# 씨네로케 (CineLoca)

영화·드라마 속 부산 촬영지를 검색하고, 지도에서 탐색하고, 여행 코스를 만들고, 방문을 인증하는 AI 안내 반응형 웹 서비스입니다.
소프트웨어공학 15팀 「하자제발」 팀 프로젝트입니다.

## 핵심 기능

| ID | 기능 | AI 사용 |
|---|---|---|
| F1 | 작품 아카이브 + AI 작품 소개 | LLM: 근거 기반 소개 생성 |
| F2 | 지도 기반 촬영지 탐색 | 없음 |
| F3 | 추천·나만의·AI 맞춤 코스 | LLM: 취향 구조화, 코스 소개 |
| F4 | 자연어 AI 대화형 탐색 | LLM: 조건 추출, 결과 요약 |
| F5 | GPS 방문 인증·스탬프 | 없음 |
| C1 | 촬영지·작품 지식베이스 구축 | 태그·매칭 제안 (사람 검수) |
| C2 | 개인정보·저작권·출처 관리 | 없음 |

AI는 자연어를 검색 조건으로 바꾸고 결과를 설명하는 데만 씁니다.
LLM은 SQL을 만들지 않고, AI가 멈춰도 지도·필터 검색·작품 상세·방문 인증은 동작합니다.

## 기술 스택

| 영역 | 기술 |
|---|---|
| Frontend | React, Vite, TypeScript, Kakao Maps JavaScript SDK |
| Backend | Python, FastAPI, Pydantic |
| Database | PostgreSQL, pgvector |
| AI | Ollama (qwen3:8b, bge-m3), Gemini API |
| 배포 | Docker Compose |
| 테스트·CI | pytest, Vitest, Playwright, GitHub Actions |

## 팀

| 역할 | 이름 | 담당 |
|---|---|---|
| R1 팀장 | 김예준 | 백엔드 구조, 경로 최적화, 방문 인증, 통합·배포 |
| R2 | 정재원 | AI 파이프라인, LLM 연결, 개인정보 처리, AI 평가 |
| R3 | 박동준 | 프론트엔드, React 화면, 지도, UI/UX |
| R4 | 이승근 | 데이터 수집, 지식베이스, 검색 SQL, 테스트·품질 |

## 저장소 구조 (예정)

```
frontend/   React 웹 클라이언트 (COMP-01)
backend/    FastAPI 서버와 배치 (COMP-02~15)
db/         스키마·확장·시드
config/     AI·공급자 설정
eval/       AI 평가셋
poc/        착수 PoC
docs/       계획서·SRS·SDD·ADR
```

## 협업 규칙

- **브랜치**: GitHub Flow. main에 직접 push하지 않습니다. `feature/이슈번호-작업명`, `fix/…`, `docs/…`
- **커밋**: Conventional Commits와 이슈 번호. 예: `feat: 촬영지 근접 검색 API 추가 (#21)`
- **PR**: 템플릿(요구사항 ID, 테스트 결과, AI 활용 여부)을 작성하고, 리뷰 1명 승인과 CI 통과 후 Squash and merge
- **비밀키**: `.env`는 커밋하지 않고 `.env.example`만 공유합니다.

## 실행 방법

Docker Compose 구성(Phase 3) 후 작성합니다.

## 데이터 출처·이용조건

모든 결과에 출처 카드를 표시하고, 데이터 출처 화면에서 이용조건과 저장 정책을 안내합니다.
카카오 지도 응답과 영화 이미지 파일은 서버에 저장하지 않습니다.