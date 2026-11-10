"""p2 분석: 건수(RK1·TBD-02), P2(촬영지 분산), P3(장소 공유)를 계산한다.

기준(계획서 1.2): 작품 50편·촬영지 150곳 / P2 30% 이상 / P3 10% 이상
- 촬영 기록 = 두 출처를 합쳐 (작품, 장소) 쌍 중복 제거
- 구·군은 주소가 있는 씨네맵 기록에서만 알 수 있으므로, P2의 '2개 구 이상' 비율은 주소가 있는 작품을 분모로 한다.
출력: raw/records.json, results/p2.json
"""
from collections import defaultdict
from common import load, save, now, norm, district_of, DATA


def main():
    bfc = load("raw_bfc.json", DATA)
    cm = load("raw_cinemap.json", DATA)
    if bfc is None or cm is None:
        raise SystemExit("p1을 먼저 실행하세요")
    recs = {}
    for src, rows in (("bfc", bfc), ("cinemap", cm)):
        for r in rows:
            w, p = (r.get("opusNm") or "").strip(), (r.get("plcNm") or "").strip()
            if not w or not p:
                continue
            k = (norm(w), norm(p))
            rec = recs.setdefault(k, {"work": w, "place": p, "sources": [], "address": None, "release": None})
            rec["sources"].append(src)
            rec["address"] = rec["address"] or (r.get("roadNmAddr") or "").strip() or None
            rec["release"] = rec["release"] or (r.get("opnDay") or "").strip() or None
    records = list(recs.values())
    save("records.json", records, DATA)

    works = defaultdict(list)
    places = defaultdict(set)
    for r in records:
        works[norm(r["work"])].append(r)
        places[norm(r["place"])].add(norm(r["work"]))
    work_src = defaultdict(set)
    for r in records:
        work_src[norm(r["work"])].update(r["sources"])
    common = sum(1 for s in work_src.values() if {"bfc", "cinemap"} <= s)

    n_works, n_places = len(works), len(places)
    ge3 = sum(1 for rs in works.values() if len({norm(x["place"]) for x in rs}) >= 3)
    with_addr = {w: {district_of(x["address"]) for x in rs if district_of(x["address"])} for w, rs in works.items()}
    with_addr = {w: d for w, d in with_addr.items() if d}
    ge2d = sum(1 for d in with_addr.values() if len(d) >= 2)
    shared = sum(1 for ws in places.values() if len(ws) >= 2)
    place_addr = defaultdict(bool)
    for r in records:
        place_addr[norm(r["place"])] |= bool(r["address"])
    top = max(works.items(), key=lambda kv: len({norm(x["place"]) for x in kv[1]}))

    res = {
        "run_at": now(),
        "counts": {"raw_bfc": len(bfc), "raw_cinemap": len(cm), "records": len(records), "works": n_works, "places": n_places,
                   "works_in_both_sources": common},
        "rk1_tbd02": {"criterion": "작품 50편·촬영지 150곳", "pass": n_works >= 50 and n_places >= 150},
        "p2": {"works_ge3_places_pct": round(100 * ge3 / n_works, 1),
               "works_ge2_districts_pct": round(100 * ge2d / max(len(with_addr), 1), 1),
               "works_with_address": len(with_addr),
               "criterion": "촬영지 3곳 이상 작품 30% 이상, 또는 2개 구 이상에 걸친 작품 30% 이상"},
        "p3": {"places_shared_ge2_works_pct": round(100 * shared / n_places, 1), "criterion": "10% 이상"},
        "places_with_address_pct": round(100 * sum(place_addr.values()) / n_places, 1),
        "top_work": {"title": top[1][0]["work"], "places": len({norm(x["place"]) for x in top[1]})},
    }
    res["p2"]["pass"] = res["p2"]["works_ge3_places_pct"] >= 30 or res["p2"]["works_ge2_districts_pct"] >= 30
    res["p3"]["pass"] = res["p3"]["places_shared_ge2_works_pct"] >= 10
    save("p2.json", res)
    import json
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
