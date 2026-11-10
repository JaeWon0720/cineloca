# 착수 PoC (TBD-02~05, RK1·RK6·RK17)

10/27~11/10에 실행한 착수 PoC의 스크립트와 결과입니다. 결과 요약은 [`poc_report.md`](poc_report.md)에 있습니다.
C1 배치(`backend/app/batch`)는 p1·p2·p3·p4·p4b의 코드를 출발점으로 씁니다.

## 단계

| 스크립트 | 확인한 것 | 결과 파일 |
|---|---|---|
| `p1_collect.py` | 부산영상위원회 촬영지 API 2종 전체 수집, 실제 필드(P1) | `results/p1.json`, `results/mapping.json` |
| `p2_analyze.py` | 건수(TBD-02), 촬영지 분산(P2), 장소 공유(P3) | `results/p2.json` |
| `p3_geocode.py` | 주소 API 좌표 확보율·좌표계(TBD-04) | `results/p3.json` |
| `p4_match.py` | KMDb 매칭 분포, 임계값 0.9·0.6(TBD-03) | `results/p4.json` |
| `p4b_noyear.py` | 개봉일 없는 작품의 자동 연결 규칙(BR-11 추가분) 검증 | `results/p4b.json` |
| `p4c_remaining.py` | 남은 작품 재검색·방송 OTT 미수록 처리·검수 큐 | (검수표는 KMDb 정보라 커밋 안 함) |
| `p5_llm.py` | 조건 추출: 로컬 qwen3:8b vs gemini-3.5-flash(TBD-05) | `results/p5*.json` |
| `p7_amenities.py` | 코스 주변 시설 후보 수·반경(FR-414, RK17) | `results/p7.json` |
| `p6_report.py` | 위 결과를 `poc_report.md`로 모음 | `poc_report.md` |
| `review_export.py` · `suggest_queue.py` · `review_score.py` | KMDb 검수표 만들기·1차 제안·채점 | (검수표는 커밋 안 함) |

## 실행

```bash
pip install -r poc/requirements.txt
cd poc
python p1_collect.py && python p2_analyze.py   # 이후 p3, p4, p4b, p5, p7, p6 순서
```

- 키는 저장소 루트의 `.env`(또는 `poc/.env`)에 둡니다. 이름은 루트 `.env.example`을 따릅니다:
  `DATA_GO_KR_SERVICE_KEY`(p1·p2·p7, TourAPI KorService2 활용신청 필요), `JUSO_SEARCH_API_KEY`·`JUSO_COORD_API_KEY`(p3),
  `KMDB_SERVICE_KEY`(p4~), `GEMINI_API_KEY`(p5).
- 선택: `GEMINI_MODEL`(기본 gemini-3.5-flash), `OLLAMA_MODEL`(기본 qwen3:8b), `OLLAMA_BASE_URL`(기본 http://127.0.0.1:11434).
- Gemini 무료 한도는 모델당 하루 20회라 `p5_llm.py --gemini-only`로 문항별로 이어서 실행합니다.

## 커밋하지 않는 것

- `poc/raw/`: 수집 원자료와 KMDb 응답(KMDb 저장 조건 TBD-01 확인 전). 필요하면 R1에게 받습니다.
- KMDb 검수표(`results/review_*.csv`, `human_check_*.csv`, `p4*_review.csv`, `p4c*.json`)
- `.env`(API 키)
