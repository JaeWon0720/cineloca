"""검수 큐 1차 제안(AI 보조, 사람이 확정): review_queue.csv에 '제안'·'확신도'·'근거' 칸을 채운다. decision 칸은 비워 둔다.

근거(점수):
  +3 KMDb 촬영 장소(fLocation)에 '부산' 또는 이 작품의 촬영지 이름이 있음
  +1 줄거리·키워드에 '부산'
  +2 개봉일을 아는 작품에서 KMDb 개봉연도(없으면 제작연도 +0~1년)가 같음 / -2 두 해 이상 다름
  +1 제목이 정규화 후 같음(다른 제목 포함) / 같지 않으면 후보에서 뺌
  -1 애니메이션·다큐멘터리·기록물·광고 등 극영화가 아닌 유형(제목에 방송사·OTT 표기가 있으면 시리즈는 감점 안 함)
확신도: 높음(최고점 ≥ 4이고 2위와 2점 이상 차이) / 보통(최고점 ≥ 2이고 1점 이상 차이) / 낮음(그 밖)
같은 제목 후보가 없으면 '미수록 제안'(방송·OTT 표기 작품) 또는 '직접 검색 필요'.
"""
import csv
import re
import time
from collections import defaultdict
from common import env, get, load, norm, DATA, RESULTS

URL = "http://api.koreafilm.or.kr/openapi-data2/wisenut/search_api/search_json2.jsp"
PFX = re.compile(r"^(JTBC|KBS2?|MBC|SBS|tvN|OCN|ENA|MBN|TV조선|채널A|EBS|넷플릭스|티빙|디즈니\+|웨이브|쿠팡플레이|왓챠|애플TV\+|"
                 r"아마존 프라임|시즌|U\+|카카오TV)\s+", re.I)
NONFILM = ("애니메이션", "다큐멘터리", "기록물", "광고홍보물", "TV-애니메이션", "실험영화")


def yr(s):
    m = re.search(r"(19|20)\d\d", s or "")
    return int(m.group(0)) if m else None


def clean(t):
    return re.sub(r"\s*!H[SE]\s*", "", t or "").strip()


def main():
    key = env("KMDB_SERVICE_KEY")
    recs = load("records.json", DATA)
    places = defaultdict(set)
    for r in recs:
        places[norm(r["work"])].add(norm(r["place"]))
    path = RESULTS / "review_queue.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    head = list(rows[0].keys())
    for c in ("제안(AI)", "확신도", "근거"):
        if c not in head:
            head.insert(head.index("decision(1~5 또는 미수록)"), c)
    out = []
    for r in rows:
        title = r["작품(촬영지 데이터)"]
        q = PFX.sub("", title).strip()
        tv = bool(PFX.match(title))
        rel = yr(r["개봉일"])
        k = norm(title)
        def search(**p):
            time.sleep(0.05)
            return (get(URL, params={"collection": "kmdb_new2", "ServiceKey": key, "detail": "Y", **p}).json()
                    .get("Data") or [{}])[0].get("Result") or []
        res = search(query=q, listCount=20)
        def has_exact(rs):
            return any(norm(clean(x.get("title"))) == norm(q) for x in rs)
        if not has_exact(res):  # 2차: 결과 100개, 개봉일을 알면 연도 범위로
            extra = search(query=q, listCount=100)
            if rel:
                extra += search(query=q, listCount=50, releaseDts=f"{rel-1}0101", releaseDte=f"{rel+1}1231")
            seen = {x.get("DOCID") for x in res}
            res += [x for x in extra if x.get("DOCID") not in seen]
        listed = {}
        for i in range(1, 6):
            m = re.search(r"/movie/([A-Z])/(\d+)", r.get(f"후보{i} 링크", ""))
            if m:
                listed[m.group(1) + m.group(2)] = i
        scored = []
        for x in res:
            t = clean(x.get("title"))
            etc = [clean(e) for e in (x.get("titleEtc") or "").split("^") if e]
            if norm(t) != norm(q) and not any(norm(e) == norm(q) for e in etc):
                continue
            fl = x.get("fLocation") or ""
            plot = " ".join(p.get("plotText", "") for p in (x.get("plots") or {}).get("plot", []))
            kw = x.get("keywords") or ""
            ry = yr(x.get("repRlsDate")) or (yr(x.get("prodYear")) + 1 if yr(x.get("prodYear")) else None)
            py = yr(x.get("prodYear"))
            s, why = 1, ["제목 같음"]
            flset = {norm(f) for f in fl.split(",") if f.strip()}
            hit = flset & places[k]
            if "부산" in fl or hit:
                s += 3
                why.append("KMDb 촬영 장소에 " + ("우리 촬영지 '" + next(iter(hit)) + "'" if hit else "'부산'"))
            if "부산" in plot or "부산" in kw:
                s += 1
                why.append("줄거리·키워드에 부산")
            if rel:
                if (yr(x.get("repRlsDate")) == rel) or (py in (rel, rel - 1)):
                    s += 2
                    why.append(f"개봉연도 {rel} 일치")
                elif ry and abs(ry - rel) >= 2:
                    s -= 2
                    why.append(f"연도 불일치({x.get('prodYear')})")
            ty = x.get("type") or ""
            series = ty in ("온라인 시리즈", "TV-시리즈", "온라인 영화", "TV-드라마")
            if tv and series:
                s += 1
                why.append(f"방송·OTT 표기와 유형({ty}) 일치")
            elif tv and ty == "극영화" and not ("부산" in fl or hit):
                s -= 1
                why.append("방송·OTT 표기인데 극영화")
            if ty in NONFILM or (ty == "온라인 시리즈" and not tv):
                s -= 1
                why.append(f"유형 {ty}")
            d = (x.get("directors") or {}).get("director") or []
            dn = d[0].get("directorNm", "") if d else ""
            docid = x.get("DOCID")
            scored.append((s, docid, t, x.get("prodYear"), ty, dn, why))
        scored.sort(key=lambda z: -z[0])
        if not scored:
            sug, conf, why = ("미수록" if tv else "직접 검색 필요"), ("보통" if tv else "낮음"), \
                ("방송·OTT 작품이고 KMDb에 같은 제목 없음" if tv else "KMDb에 같은 제목 없음(제목 표기 차이 가능)")
        else:
            top = scored[0]
            gap = top[0] - (scored[1][0] if len(scored) > 1 else 0)
            conf = "높음" if top[0] >= 4 and gap >= 2 else "보통" if top[0] >= 2 and gap >= 1 else "낮음"
            if top[4] in NONFILM and not any("촬영 장소" in w for w in top[6]):
                conf = "낮음"  # 영화가 아닌 유형이고 부산 근거 없음
            num = listed.get(top[1])
            sug = str(num) if num else top[1]
            why = f"{top[2]}({top[3]}, {top[4]}, 감독 {top[5] or '-'}): " + ", ".join(top[6])
            if len(scored) > 1:
                why += f" / 같은 제목 {len(scored)}편"
            if tv and top[0] < 1:
                sug, conf = "미수록", "보통"
                why = "방송·OTT 작품으로 보이고 같은 제목 영화의 부산 근거 없음 — " + why
        r["제안(AI)"], r["확신도"], r["근거"] = sug, conf, why
        out.append(r)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=head)
        w.writeheader()
        w.writerows(out)
    from collections import Counter
    print("확신도", Counter(r["확신도"] for r in out))
    print("제안 종류", Counter("후보 번호" if r["제안(AI)"].isdigit() else ("작품 코드" if re.match(r"[A-Z]\d", r["제안(AI)"]) else r["제안(AI)"]) for r in out))


if __name__ == "__main__":
    main()
