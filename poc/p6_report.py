"""p6 보고서: p1~p5·p7 결과를 poc_report.md 한 파일로 모은다. 실행하지 않은 단계는 '대기'로 표시한다."""
from common import load, now, ROOT


def fmt(v, unit=""):
    return "—" if v is None else f"{v}{unit}"


def main():
    p1, p2, p3, p4, p5, p7 = (load(f"p{i}.json") for i in (1, 2, 3, 4, 5, 7))
    L = [f"# 착수 PoC 보고서", "", f"생성: {now()} · 원자료: poc/results/*.json", "",
         "| 단계 | 기준 | 결과 | 판정 |", "|---|---|---|---|"]
    if p1:
        s = p1["sources"]
        L.append(f"| p1 수집 | 실제 필드 | 영화 속 부산정보 {s['bfc']['collected']}건 {list(s['bfc']['fields'])}, "
                 f"씨네맵 {s['cinemap']['collected']}건 {list(s['cinemap']['fields'])} | {p1['p1_check']['verdict']} |")
    else:
        L.append("| p1 수집 | 실제 필드 | 대기(DATA_GO_KR_SERVICE_KEY) | — |")
    if p2:
        c = p2["counts"]
        L.append(f"| p2 건수 | 작품 50·촬영지 150 | 촬영 기록 원본 {c['raw_bfc']+c['raw_cinemap']:,}행((작품, 장소) 중복 제거 {c['records']:,}건), 작품 {c['works']}편, 장소 {c['places']:,}곳, "
                 f"공통 작품 {c['works_in_both_sources']}편 | {'통과' if p2['rk1_tbd02']['pass'] else '미달'} |")
        L.append(f"| p2 P2 | 30% | 2개 구 이상 {p2['p2']['works_ge2_districts_pct']}%(주소 있는 작품 {p2['p2']['works_with_address']}편 기준), "
                 f"촬영지 3곳 이상 {p2['p2']['works_ge3_places_pct']}% | {'통과' if p2['p2']['pass'] else '미달'} |")
        L.append(f"| p2 P3 | 10% | 2편 이상 촬영된 장소 {p2['p3']['places_shared_ge2_works_pct']}% | "
                 f"{'통과' if p2['p3']['pass'] else '미달'} |")
    else:
        L.append("| p2 분석 | 건수·P2·P3 | 대기(p1 뒤) | — |")
    if p3:
        L.append(f"| p3 좌표 | 확보율 기록, 부산 범위 100% 근접 | 주소검색 성공 {p3['address_search_success_pct']}%, 좌표 확보 "
                 f"{p3['coord_rate_pct']}%(주소 있는 장소 {p3['coord_rate_among_address_pct']}%), {p3['crs']}, 부산 범위 안 "
                 f"{p3['in_busan_bbox_pct']}% | 기록 |")
    else:
        L.append("| p3 좌표 | 확보율·좌표계 | 대기(JUSO_SEARCH_API_KEY·JUSO_COORD_API_KEY) | — |")
    if p4:
        L.append(f"| p4 매칭 | 자동연결 정확도 95% | 자동 {p4['auto_pct']}%, 검수 큐 {p4['review_pct']}%, 미매칭 {p4['unmatched_pct']}%, "
                 f"정확도 {fmt(p4['auto_accuracy_pct'], '%')} | {'통과' if p4.get('pass') else '판정 대기' if p4.get('pass') is None else '미달'} |")
    else:
        L.append("| p4 매칭 | 정확도 95% | 대기(KMDB_SERVICE_KEY) | — |")
    if p5:
        for r in p5["runs"]:
            done = r.get("complete", True)
            L.append(f"| p5 LLM ({r['provider']}, 동시 {r['workers']}) | 필드 일치율 90% | "
                     + ("" if done else f"{r['questions']}/22문항까지: ")
                     + f"일치율 {r['field_match_pct']}%, 스키마 {r['schema_ok_pct']}%, "
                     + (f"범위 밖 {r['out_of_scope_correct']}/2, " if done else "")
                     + f"p95 {r['latency_p95_s']}초 | {('통과' if r['pass'] else '미달') if done else '진행 중'} |")
        if p5.get("gemini_error"):
            L.append(f"| p5 LLM (Gemini) | - | 중단 사유: {p5['gemini_error']} | — |")
    if p7:
        z, m = p7["zero_pct"], p7["median"]
        L.append(f"| p7 주변 시설 | 0건 구간 50% 이하 | 음식점 500m 0건 {z['food_500m']}%(중앙값 {m['food_500m']}), 1km 0건 "
                 f"{z['food_1km']}%; 숙박 1km 0건 {z['lodging_1km']}%(중앙값 {m['lodging_1km']}), 2km 0건 {z['lodging_2km']}%; "
                 f"p95 {p7['latency_p95_s']}초 | {'통과' if z['food_500m'] <= 50 and z['lodging_1km'] <= 50 else '반경 재검토'} |")
    else:
        L.append("| p7 주변 시설 | 0건 구간 50% 이하 | 대기(DATA_GO_KR_SERVICE_KEY·KorService2 활용신청, p3 좌표) | — |")
    L += ["", "수작업 칸: P5 기존 서비스 조사, TBD-01 KMDb 약관, TBD-09 외부 공개 터널 허용 여부"]
    (ROOT / "poc_report.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
