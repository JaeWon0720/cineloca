"""KMDb 수작업 결과 채점: review_accuracy.csv의 correct, review_queue.csv의 decision을 읽어 p4.json과 review_decisions.json에 기록한다."""
import csv
import json
import re
from common import load, save, now, RESULTS


def main():
    acc = list(csv.DictReader(open(RESULTS / "review_accuracy.csv", encoding="utf-8-sig")))
    res = {"run_at": now()}
    for grp in ("p4(개봉일 있음)", "p4b(개봉일 없음)"):
        rows = [r for r in acc if r["구분"] == grp]
        done = [r for r in rows if r["correct(Y/N)"].strip().upper() in ("Y", "N")]
        ok = sum(r["correct(Y/N)"].strip().upper() == "Y" for r in done)
        res[grp] = {"total": len(rows), "judged": len(done), "correct": ok,
                    "accuracy_pct": round(100 * ok / len(done), 1) if done else None,
                    "wrong": [r["작품(촬영지 데이터)"] for r in done if r["correct(Y/N)"].strip().upper() == "N"]}
    q = list(csv.DictReader(open(RESULTS / "review_queue.csv", encoding="utf-8-sig")))
    dec = {}
    for r in q:
        v = r["decision(1~5 또는 미수록)"].strip()
        if v in ("1", "2", "3", "4", "5"):
            link = r[f"후보{v} 링크"]
            m = re.search(r"/movie/([A-Z])/(\d+)", link)
            dec[r["작품(촬영지 데이터)"]] = {"docid": m.group(1) + m.group(2) if m else None, "by": r["담당"]}
        elif re.fullmatch(r"[A-Z]\d{4,6}", v):  # 후보에 없어 KMDb에서 직접 찾은 작품 코드
            dec[r["작품(촬영지 데이터)"]] = {"docid": v, "by": r["담당"]}
        elif v == "미수록":
            dec[r["작품(촬영지 데이터)"]] = {"docid": None, "by": r["담당"]}
    res["queue"] = {"total": len(q), "decided": len(dec), "linked": sum(1 for d in dec.values() if d["docid"]),
                    "absent": sum(1 for d in dec.values() if not d["docid"]),
                    "left_by_owner": {o: sum(1 for r in q if r["담당"] == o and r["작품(촬영지 데이터)"] not in dec) for o in ("R1", "R2", "R3", "R4")}}
    save("review_decisions.json", dec)
    p4 = load("p4.json")
    a = res["p4(개봉일 있음)"]
    if a["judged"]:
        p4.update({"auto_accuracy_pct": a["accuracy_pct"], "judged": a["judged"], "pass": a["accuracy_pct"] >= 95})
    b = res["p4b(개봉일 없음)"]
    if b["judged"]:
        p4["rule_c_accuracy_pct"] = b["accuracy_pct"]
    save("p4.json", p4)
    save("review_score.json", res)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
