"""p4c 개봉일 없는 작품 중 규칙 C(p4b)로도 남은 작품의 처리량 확인(11/9).

1) 제목 앞 방송사·플랫폼 이름(넷플릭스, JTBC 등)을 떼고, 검색 결과를 50개까지 받아 다시 찾는다 → 규칙 C로 자동 연결
2) 방송사·플랫폼 표기가 있고 같은 제목 후보가 없으면 'KMDb 미수록'(FR-604, 촬영지 정보만 제공)으로 자동 처리
3) 나머지는 사람 검수 큐: 후보(같은 제목, 연도·유형·촬영 장소)를 붙인 results/p4c_review.csv로 내보낸다
검수 시간은 1편당 1~2분(후보가 준비된 상태에서 KMDb 링크·연도·촬영 장소 확인)으로 추정한다.
출력: results/p4c.json, results/p4c_review.csv
"""
import csv
import re
import time
from collections import defaultdict, Counter
from common import env, get, load, save, now, norm, DATA, RESULTS

URL = "http://api.koreafilm.or.kr/openapi-data2/wisenut/search_api/search_json2.jsp"
PFX = re.compile(r"^(JTBC|KBS2?|MBC|SBS|tvN|OCN|ENA|MBN|TV조선|채널A|EBS|넷플릭스|티빙|디즈니\+|웨이브|쿠팡플레이|왓챠|애플TV\+|"
                 r"아마존 프라임|시즌|U\+|카카오TV)\s+", re.I)


def yr(s):
    m = re.search(r"(19|20)\d\d", s or "")
    return int(m.group(0)) if m else None


def main():
    key = env("KMDB_SERVICE_KEY")
    det, recs = load("kmdb_detail.json", DATA), load("records.json", DATA)
    places = defaultdict(set)
    for r in recs:
        places[norm(r["work"])].add(norm(r["place"]))
    clean = lambda t: re.sub(r"\s*!H[SE]\s*", "", t or "").strip()

    def exact(w, c):
        return norm(c["title"]) == norm(w) or any(norm(x) == norm(w) for x in (c.get("titleEtc") or "").split("^") if x)

    def ev(k, c):
        fl = c.get("fLocation") or ""
        return "부산" in fl or bool({norm(x) for x in fl.split(",") if x.strip()} & places[k])

    noyear = {k: v for k, v in det.items() if not yr(v["release"])}
    first = {k for k, v in noyear.items() if len([c for c in v["cands"] if exact(v["title"], c) and ev(k, c)]) == 1}
    rows = []
    for k, v in noyear.items():
        if k in first:
            continue
        q = PFX.sub("", v["title"]).strip()
        d = (get(URL, params={"collection": "kmdb_new2", "ServiceKey": key, "detail": "Y", "query": q, "listCount": 50}).json()
             .get("Data") or [{}])[0]
        cands = [{"docid": x.get("DOCID"), "title": clean(x.get("title")), "prodYear": x.get("prodYear"), "type": x.get("type"),
                  "fLocation": x.get("fLocation"), "titleEtc": x.get("titleEtc")} for x in d.get("Result") or []]
        ex = [c for c in cands if exact(q, c)]
        exe = [c for c in ex if ev(k, c)]
        if len(exe) == 1:
            res = "auto"
        elif PFX.match(v["title"]) and not ex:
            res = "kmdb_absent_tv"
        elif not ex:
            res = "review_no_exact"
        else:
            res = "review"
        rows.append({"title": v["title"], "query": q, "result": res, "places": len(places[k]),
                     "candidates": [{**c, "busan_evidence": ev(k, c)} for c in ex][:5],
                     "pick": exe[0]["docid"] if len(exe) == 1 else None})
        time.sleep(0.05)
    cnt = Counter(r["result"] for r in rows)
    review = [r for r in rows if r["result"].startswith("review")]
    with open(RESULTS / "p4c_review.csv", "w", newline="") as fp:
        wr = csv.writer(fp)
        wr.writerow(["work", "촬영지 수", "후보(제목/연도/유형/부산 근거)", "decision(docid 또는 '미수록')"])
        for r in sorted(review, key=lambda r: -r["places"]):
            wr.writerow([r["title"], r["places"], " | ".join(f"{c['title']}/{c['prodYear']}/{c['type']}/{'부산' if c['busan_evidence'] else '-'}"
                                                           for c in r["candidates"]) or "(같은 제목 후보 없음)", ""])
    p4 = load("p4.json")
    cand = load("kmdb_candidates.json", DATA)
    n = len(cand)
    known_review = sum(1 for o in cand if o["release"] and o["decision"] == "review")
    res = {"run_at": now(), "noyear_works": len(noyear), "auto_rule_c_first": len(first), "remaining_after_first": len(rows),
           "retry": dict(cnt), "human_review_noyear": len(review), "human_review_known_year": known_review,
           "human_review_total": len(review) + known_review,
           "effort_estimate": {"min_per_work": [1, 2], "total_hours": [round((len(review) + known_review) / 60, 1),
                                                                     round(2 * (len(review) + known_review) / 60, 1)],
                               "per_person_4_min": [round((len(review) + known_review) / 4), round(2 * (len(review) + known_review) / 4)]},
           "final_distribution_pct": {
               "auto": round(100 * (p4["with_rule_c"]["auto"] * n / 100 + cnt.get("auto", 0)) / n, 1),
               "kmdb_absent_auto": round(100 * cnt.get("kmdb_absent_tv", 0) / n, 1),
               "human_review": round(100 * (len(review) + known_review) / n, 1)},
           "detail": rows}
    save("p4c.json", res)
    print({k: v for k, v in res.items() if k != "detail"})


if __name__ == "__main__":
    main()
