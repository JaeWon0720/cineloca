# 씨네로케 프론트엔드 (COMP-01)

React + Vite + TypeScript. Node.js 24 기준.

| 명령 | 설명 |
|---|---|
| `npm install` | 패키지 설치 |
| `npm run dev` | 개발 서버 (http://localhost:5173) |
| `npm run test` | Vitest 테스트 |
| `npm run lint` | 코드 검사 (oxlint) |
| `npm run build` | 타입 검사 + 배포용 빌드 |

## 화면 주소 (SRS 6.1)

| 주소 | 화면 |
|---|---|
| `/` | UI-01 지도 홈 |
| `/works` | UI-03 작품 목록 |
| `/ask` | UI-05 AI 탐색 |
| `/courses` | UI-06 코스 |
| `/me` | UI-08 내 기록 |
| `/sources` | UI-09 데이터 출처·이용 안내 |
| `/admin` | UI-10~12 관리자 (하단 탭 없음) |

## 목업 API (백엔드 연결 전)

백엔드에 아직 없는 API가 있어서, 기본값은 `src/mocks/`의 목업 API로 화면이 동작합니다. 상단에 노란 안내 띠가 보이면 목업 모드입니다.

- `src/api/types*.ts`: 화면이 쓰는 API 응답 타입. 백엔드 `app/api/schemas`와 같은 모양으로 유지합니다.
- `src/mocks/handlers/`: 기능별 가짜 응답. `src/mocks/data/`의 예시 데이터를 씁니다(화면 개발용, 좌표는 근사값).
- 실제 백엔드로 연결하려면 저장소 루트 `.env`에 `VITE_API_MOCK=false`를 넣고 FastAPI를 8000번 포트로 띄우세요. 개발 서버가 `/api`를 그쪽으로 넘깁니다.
- 테스트는 항상 목업 API로 돕니다.
