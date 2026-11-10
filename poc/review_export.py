"""KMDb 수작업용 검수표 2개를 만든다(엑셀에서 바로 열리게 UTF-8 BOM CSV).

1) results/review_accuracy.csv — 자동 연결이 맞는지 판정(정확도 기준 95%, TBD-03)
   - p4: 개봉일 있는 작품의 자동 연결 표본 50건, p4b: 개봉일 없는 작품의 규칙 C 자동 연결 54건 전부
   - 판정 칸 correct: Y(같은 작품) / N(다른 작품)
2) results/review_queue.csv — 사람이 직접 고르는 검수 큐 121편(개봉일 없는 103편 + 점수 0.6~0.9인 개봉일 있는 18편)
   - 후보 최대 5개(제목/연도/유형/부산 근거/링크), 판정 칸 decision: 후보 번호(1~5) 또는 '미수록'
   - 담당(R1~R4)은 촬영지 수가 많은 작품부터 돌아가며 배정
채점: python review_score.py
"""
import csv
from collections import defaultdict
from common import load, norm, DATA, RESULTS


def url(docid):
    return f"https://www.kmdb.or.kr/db/kor/detail/movie/{docid[0]}/{docid[1:]}" if docid else ""


def main():
    recs = load("records.json", DATA)
    places = defaultdict(list)
    for r in recs:
        if r["place"] not in places[norm(r["work"])]:
            places[norm(r["work"])].append(r["place"])
    det = load("kmdb_detail.json", DATA)
    cand = load("kmdb_candidates.json", DATA)
    p4c = load("p4c.json")

    # 1) 정확도 검수
    rows = []
    for r in csv.DictReader(open(RESULTS / "p4_review.csv", encoding="utf-8")):
        rows.append(["p4(개봉일 있음)", r["work"], r["release"], r["kmdb_title"], r["kmdb_year"], url(r["docid"]),
                     " · ".join(places[norm(r["work"])][:3]), ""])
    for r in csv.DictReader(open(RESULTS / "p4b_review.csv", encoding="utf-8")):
        rows.append(["p4b(개봉일 없음)", r["work"], "", r["kmdb_title"], r["kmdb_prodYear"], url(r["docid"]),
                     " · ".join(places[norm(r["work"])][:3]), ""])
    ap = RESULTS / "review_accuracy.csv"
    filled = ap.exists() and any(r.get("correct(Y/N)", "").strip() for r in csv.DictReader(open(ap, encoding="utf-8-sig")))
    if filled and "--force" not in __import__("sys").argv:
        print("review_accuracy.csv에 판정이 있어 덮어쓰지 않음(다시 만들려면 --force)")
        rows = []
    else:
      with open(ap, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["구분", "작품(촬영지 데이터)", "개봉일", "KMDb 제목", "KMDb 연도", "KMDb 링크", "촬영지(앞 3곳)", "correct(Y/N)"])
        w.writerows(rows)

    # 2) 검수 큐
    q = []
    for d in p4c["detail"]:
        if not d["result"].startswith("review"):
            continue
        cs = [(c["title"], c["prodYear"], c["type"], "부산" if c["busan_evidence"] else "-", url(c["docid"])) for c in d["candidates"]]
        q.append((d["title"], "", d["places"], cs))
    for o in cand:
        if o["release"] and o["decision"] == "review":
            k = norm(o["work"])
            cs = [(c["title"], c["prodYear"], c["type"], "부산" if "부산" in (c.get("fLocation") or "") else "-", url(c["docid"]))
                  for c in det[k]["cands"][:5]]
            q.append((o["work"], o["release"], len(places[k]), cs))
    q.sort(key=lambda x: -x[2])
    qp = RESULTS / "review_queue.csv"
    if qp.exists() and "제안(AI)" in qp.read_text(encoding="utf-8-sig").splitlines()[0] and "--force" not in __import__("sys").argv:
        print("review_queue.csv에 1차 제안·판정이 있어 덮어쓰지 않음(다시 만들려면 --force)")
        q = None
    if q is None:
        print(f"review_accuracy.csv {len(rows)}건")
        return
    with open(RESULTS / "review_queue.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        head = ["담당", "작품(촬영지 데이터)", "개봉일", "촬영지 수", "촬영지(앞 3곳)"]
        for i in range(1, 6):
            head += [f"후보{i}(제목/연도/유형/부산 근거)", f"후보{i} 링크"]
        w.writerow(head + ["decision(1~5 또는 미수록)"])
        for i, (title, rel, n, cs) in enumerate(q):
            row = [f"R{i % 4 + 1}", title, rel, n, " · ".join(places[norm(title)][:3])]
            for j in range(5):
                if j < len(cs):
                    t, y, ty, ev, u = cs[j]
                    row += [f"{t} / {y or '-'} / {ty or '-'} / {ev}", u]
                else:
                    row += ["", ""]
            w.writerow(row + [""])
    print(f"review_accuracy.csv {len(rows)}건, review_queue.csv {len(q)}편")


if __name__ == "__main__":
    main()
