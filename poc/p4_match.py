"""p4 매칭: 촬영지 데이터의 작품을 KMDb(DATA-04)에 매칭하고 임계값 0.9·0.6(BR-11, TBD-03)의 분포를 본다.

점수 = 0.75 × 제목 유사도(정규화 후 rapidfuzz ratio) + 0.25 × 연도 점수(개봉연도 같음 1, ±1년 0.5, 모름 0.5, 그 밖 0)
  0.9 이상 자동 연결 / 0.6 이상 검수 큐 / 미만 미매칭
정확도(기준 95%)는 사람이 판정해야 하므로, 자동 연결 중 무작위 50건을 results/p4_review.csv로 내보낸다.
R4가 'correct' 칸에 Y/N을 채운 뒤 `python p4_match.py --score`로 정확도를 계산한다.
키: KMDB_SERVICE_KEY
출력: raw/kmdb_candidates.json, results/p4.json, results/p4_review.csv
"""
import csv
import random
import re
import sys
import time
from rapidfuzz import fuzz
from common import env, get, load, save, now, norm, DATA, RESULTS

URL = "http://api.koreafilm.or.kr/openapi-data2/wisenut/search_api/search_json2.jsp"
AUTO, REVIEW = 0.9, 0.6


def clean_title(t):
    return re.sub(r"\s*!H[SE]\s*", "", t or "").strip()


def year_of(s):
    m = re.search(r"(19|20)\d\d", s or "")
    return int(m.group(0)) if m else None


def search(key, title):
    r = get(URL, params={"collection": "kmdb_new2", "ServiceKey": key, "detail": "N", "query": title, "listCount": 10})
    body = r.json()
    data = (body.get("Data") or [{}])[0]
    return [{"docid": x.get("DOCID"), "title": clean_title(x.get("title")), "prodYear": x.get("prodYear"),
             "repRlsDate": x.get("repRlsDate"), "type": x.get("type"), "genre": x.get("genre")}
            for x in (data.get("Result") or [])]


def score(work, rel_year, cand):
    t = fuzz.ratio(norm(work), norm(cand["title"])) / 100
    ry_c, py_c = year_of(cand.get("repRlsDate")), year_of(cand.get("prodYear"))
    if rel_year is None or (ry_c is None and py_c is None):
        y = 0.5
    elif ry_c is not None:
        y = 1.0 if ry_c == rel_year else 0.5 if abs(ry_c - rel_year) == 1 else 0.0
    else:  # 제작연도는 개봉 전해일 수 있음
        y = 1.0 if py_c in (rel_year, rel_year - 1) else 0.5 if abs(py_c - rel_year) <= 2 else 0.0
    return round(0.75 * t + 0.25 * y, 3)


def run():
    key = env("KMDB_SERVICE_KEY")
    records = load("records.json", DATA)
    if records is None:
        raise SystemExit("p2를 먼저 실행하세요")
    works = {}
    for r in records:
        w = works.setdefault(norm(r["work"]), {"title": r["work"], "release": None})
        w["release"] = w["release"] or r.get("release")
    out = []
    for k, w in works.items():
        cands = search(key, w["title"])
        ry = year_of(w["release"])
        scored = sorted(({**c, "score": score(w["title"], ry, c)} for c in cands), key=lambda c: -c["score"])
        best = scored[0] if scored else None
        s = best["score"] if best else 0
        out.append({"work": w["title"], "release": w["release"], "best": best, "n_candidates": len(cands),
                    "decision": "auto" if s >= AUTO else "review" if s >= REVIEW else "unmatched"})
        time.sleep(0.1)
    save("kmdb_candidates.json", out, DATA)
    n = len(out)
    cnt = {d: sum(1 for o in out if o["decision"] == d) for d in ("auto", "review", "unmatched")}
    autos = [o for o in out if o["decision"] == "auto"]
    random.seed(1109)
    sample = random.sample(autos, min(50, len(autos)))
    with open(RESULTS / "p4_review.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["work", "release", "kmdb_title", "kmdb_year", "docid", "score", "correct(Y/N)"])
        for o in sample:
            b = o["best"]
            wr.writerow([o["work"], o["release"], b["title"], b.get("repRlsDate") or b.get("prodYear"), b["docid"], b["score"], ""])
    res = {"run_at": now(), "works": n, "thresholds": [AUTO, REVIEW],
           "auto_pct": round(100 * cnt["auto"] / n, 1), "review_pct": round(100 * cnt["review"] / n, 1),
           "unmatched_pct": round(100 * cnt["unmatched"] / n, 1),
           "by_release_known": {k: {d: round(100 * sum(1 for o in out if bool(o["release"]) == (k == "known") and o["decision"] == d)
                                              / max(sum(1 for o in out if bool(o["release"]) == (k == "known")), 1), 1)
                                    for d in ("auto", "review", "unmatched")} for k in ("known", "unknown")},
           "tv_ott_prefixed_titles": sum(1 for o in out if re.match(r"(JTBC|KBS|MBC|SBS|tvN|OCN|ENA|넷플릭스|티빙|디즈니\+|웨이브|쿠팡)", o["work"])),
           "auto_accuracy_pct": None, "review_sample": "results/p4_review.csv (사람 판정 필요)"}
    save("p4.json", res)
    print(res)


def score_review():
    rows = list(csv.DictReader(open(RESULTS / "p4_review.csv")))
    done = [r for r in rows if r["correct(Y/N)"].strip().upper() in ("Y", "N")]
    if not done:
        raise SystemExit("p4_review.csv의 correct 칸을 먼저 채우세요")
    acc = round(100 * sum(r["correct(Y/N)"].strip().upper() == "Y" for r in done) / len(done), 1)
    res = load("p4.json")
    res.update({"auto_accuracy_pct": acc, "judged": len(done), "pass": acc >= 95})
    save("p4.json", res)
    print(res)


if __name__ == "__main__":
    score_review() if "--score" in sys.argv else run()
