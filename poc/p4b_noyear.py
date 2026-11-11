"""p4b 개봉일 없는 작품의 자동 연결 규칙 검증(TBD-03 보완, 11/9).

씨네맵에만 있는 작품은 개봉일이 없어 p4 점수(제목 0.75 + 연도 0.25)로는 0.9에 닿지 못한다.
규칙 C: KMDb 후보 중 제목(또는 다른 제목 titleEtc)이 정규화 후 완전히 같고, KMDb 촬영 장소(fLocation)에 '부산'이 있거나
우리 촬영지 이름과 겹치는 후보가 정확히 하나면 자동 연결한다.
검증: 개봉일이 있어 '제목 + 연도'로 정답이 하나로 정해지는 작품에서 연도를 지우고 규칙을 적용해 정답과 비교한다.
비교한 다른 규칙: A(같은 제목 후보가 하나), B(A + 극영화) — 개봉일 없는 작품에서 오연결 사례가 있어 채택하지 않음.
출력: results/p4b.json, results/p4b_review.csv(규칙 C로 연결된 개봉일 없는 작품, 사람 확인용)
"""
import csv
import re
import time
from collections import defaultdict
from common import env, get, load, save, now, norm, DATA, RESULTS

URL = "http://api.koreafilm.or.kr/openapi-data2/wisenut/search_api/search_json2.jsp"


def yr(s):
    m = re.search(r"(19|20)\d\d", s or "")
    return int(m.group(0)) if m else None


def clean(t):
    return re.sub(r"\s*!H[SE]\s*", "", t or "").strip()


def fetch(works):
    key = env("KMDB_SERVICE_KEY")
    out = {}
    for k, w in works.items():
        d = get(URL, params={"collection": "kmdb_new2", "ServiceKey": key, "detail": "Y", "query": w["title"], "listCount": 10}).json()
        d = (d.get("Data") or [{}])[0]
        out[k] = {"title": w["title"], "release": w["release"],
                  "cands": [{"docid": x.get("DOCID"), "title": clean(x.get("title")), "prodYear": x.get("prodYear"),
                             "repRlsDate": x.get("repRlsDate"), "type": x.get("type"), "nation": x.get("nation"),
                             "fLocation": x.get("fLocation"), "titleEtc": x.get("titleEtc")} for x in d.get("Result") or []]}
        time.sleep(0.05)
    save("kmdb_detail.json", out, DATA)
    return out


def main():
    recs = load("records.json", DATA)
    places = defaultdict(set)
    works = {}
    for r in recs:
        places[norm(r["work"])].add(norm(r["place"]))
        w = works.setdefault(norm(r["work"]), {"title": r["work"], "release": None})
        w["release"] = w["release"] or r.get("release")
    det = load("kmdb_detail.json", DATA) or fetch(works)

    def exact(w, c):
        t = norm(w)
        return norm(c["title"]) == t or any(norm(x) == t for x in (c.get("titleEtc") or "").split("^") if x)

    def evidence(k, c):
        fl = c.get("fLocation") or ""
        return "부산" in fl or bool({norm(x) for x in fl.split(",") if x.strip()} & places[k])

    def rule_a(k, v):
        ex = [c for c in v["cands"] if exact(v["title"], c)]
        return ex[0] if len(ex) == 1 else None

    def rule_b(k, v):
        ex = [c for c in v["cands"] if exact(v["title"], c) and c["type"] == "극영화"]
        return ex[0] if len(ex) == 1 else None

    def rule_c(k, v):
        ex = [c for c in v["cands"] if exact(v["title"], c) and evidence(k, c)]
        return ex[0] if len(ex) == 1 else None

    truth = {}
    for k, v in det.items():
        y = yr(v["release"])
        if not y:
            continue
        ok = [c for c in v["cands"] if exact(v["title"], c) and
              (yr(c["repRlsDate"]) == y or (not yr(c["repRlsDate"]) and yr(c["prodYear"]) in (y, y - 1)))]
        if len(ok) == 1:
            truth[k] = ok[0]["docid"]
    noyear = [k for k, v in det.items() if not yr(v["release"])]
    res = {"run_at": now(), "validation_set": len(truth), "noyear_works": len(noyear), "rules": {}}
    for name, f in (("A_exact_unique", rule_a), ("B_exact_unique_feature", rule_b), ("C_exact_busan_evidence", rule_c)):
        made = {k: f(k, det[k]) for k in truth}
        made = {k: c for k, c in made.items() if c}
        correct = sum(1 for k, c in made.items() if c["docid"] == truth[k])
        res["rules"][name] = {"validation_linked": len(made), "validation_correct": correct,
                              "validation_precision_pct": round(100 * correct / max(len(made), 1), 1),
                              "noyear_auto": sum(1 for k in noyear if f(k, det[k]))}
    res["known_wrong_examples_rule_a"] = ["바람(1996년작에 연결, 부산 촬영작은 2009년작)", "사자(2024년 애니메이션에 연결)"]
    res["adopted"] = "C_exact_busan_evidence"
    with open(RESULTS / "p4b_review.csv", "w", newline="") as fp:
        wr = csv.writer(fp)
        wr.writerow(["work", "kmdb_title", "kmdb_prodYear", "type", "docid", "fLocation(앞부분)", "correct(Y/N)"])
        for k in noyear:
            c = rule_c(k, det[k])
            if c:
                wr.writerow([det[k]["title"], c["title"], c["prodYear"], c["type"], c["docid"], (c["fLocation"] or "")[:60], ""])
    # p4 분포 갱신: 개봉일 없는 작품 중 규칙 C로 연결되는 것을 자동으로 옮긴다
    p4 = load("p4.json")
    cand = load("kmdb_candidates.json", DATA)
    n = len(cand)
    c_auto = {norm(det[k]["title"]) for k in noyear if rule_c(k, det[k])}
    moved = sum(1 for o in cand if o["decision"] != "auto" and norm(o["work"]) in c_auto)
    base = {d: sum(1 for o in cand if o["decision"] == d) for d in ("auto", "review", "unmatched")}
    from_review = sum(1 for o in cand if o["decision"] == "review" and norm(o["work"]) in c_auto)
    after = {"auto": base["auto"] + moved, "review": base["review"] - from_review, "unmatched": base["unmatched"] - (moved - from_review)}
    res["p4_after_rule_c"] = {d: round(100 * v / n, 1) for d, v in after.items()}
    p4["with_rule_c"] = res["p4_after_rule_c"]
    save("p4.json", p4)
    save("p4b.json", res)
    print(res)


if __name__ == "__main__":
    main()
