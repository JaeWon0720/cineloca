"""p7 주변 시설 후보 수: TourAPI 위치기반 조회(DATA-13, KorService2 locationBasedList2)로 FR-414의 반경을 확인한다(RK17, TBD-10).

- 표본: 좌표가 있는 촬영지(p3 결과)에서 무작위 쌍 20구간(직선거리 0.3~3km, 같은 시드)
- 구간 중간 지점 반경 500m 안 음식점(contentTypeId 39, 카페 포함) 수, 각 구간 뒤쪽 촬영지 반경 1km 안 숙박(32) 수
- 비교용으로 1km(음식점)·2km(숙박)도 센다. 0건 구간 비율로 반경을 정한다. 호출 지연도 기록(제한 시간 3초 설계 확인)
- 분류: 응답의 cat3(또는 lclsSystm3) 코드 분포와 '카페/전통찻집' 비율을 기록해 FR-415 허용 분류 목록의 기초로 쓴다
키: DATA_GO_KR_SERVICE_KEY(국문 관광정보 서비스 KorService2 활용신청 필요)
출력: results/p7.json
"""
import math
import random
import statistics
import time
from collections import Counter
from common import env, get, load, save, now, mask, DATA

URL = "https://apis.data.go.kr/B551011/KorService2/locationBasedList2"


def hav(a, b):
    R = 6371000
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    return 2 * R * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


def nearby(key, lat, lon, radius, ctype):
    t = time.perf_counter()
    items, page = [], 1
    while True:
        r = get(URL, params={"serviceKey": key, "MobileOS": "ETC", "MobileApp": "cineloca-poc", "_type": "json",
                             "mapX": lon, "mapY": lat, "radius": radius, "contentTypeId": ctype, "arrange": "E",
                             "numOfRows": 100, "pageNo": page}, timeout=10)
        try:
            body = r.json()["response"]
        except (ValueError, KeyError):
            raise SystemExit(f"TourAPI 오류({r.status_code}): {mask(r.text[:300])}")
        if body["header"]["resultCode"] != "0000":
            raise SystemExit(f"TourAPI 오류: {body['header']}")
        b = body["body"]
        its = (b.get("items") or {}).get("item") or []
        items += its if isinstance(its, list) else [its]
        if len(items) >= int(b.get("totalCount") or 0) or not its:
            break
        page += 1
    return items, time.perf_counter() - t


def main():
    key = env("DATA_GO_KR_SERVICE_KEY")
    geo = load("geocoded.json", DATA)
    if not geo:
        raise SystemExit("p3를 먼저 실행하세요(좌표 필요)")
    pts = [(g["place"], (g["coord"]["lat"], g["coord"]["lon"])) for g in geo if g.get("coord") and g.get("via") == "address"]
    random.seed(1109)
    pairs = []
    while len(pairs) < 20:
        a, b = random.sample(pts, 2)
        if 300 <= hav(a[1], b[1]) <= 3000:
            pairs.append((a, b))
    segs, lat_s, cats, cafe, food_total = [], [], Counter(), 0, 0
    for a, b in pairs:
        mid = ((a[1][0] + b[1][0]) / 2, (a[1][1] + b[1][1]) / 2)
        f1k, dt1 = nearby(key, mid[0], mid[1], 1000, 39)
        l2k, dt2 = nearby(key, b[1][0], b[1][1], 2000, 32)
        lat_s += [dt1, dt2]
        f500 = [i for i in f1k if float(i.get("dist") or 1e9) <= 500]
        l1k = [i for i in l2k if float(i.get("dist") or 1e9) <= 1000]
        for i in f1k:
            c = i.get("lclsSystm3") or i.get("cat3")
            cats[c] += 1
            cafe += c in ("A05020900", "FD050100") or "카페" in (i.get("title") or "")
        food_total += len(f1k)
        segs.append({"from": a[0], "to": b[0], "dist_m": round(hav(a[1], b[1])), "food_500m": len(f500), "food_1km": len(f1k),
                     "lodging_1km": len(l1k), "lodging_2km": len(l2k)})
        time.sleep(0.1)
    zero = lambda k: round(100 * sum(1 for s in segs if s[k] == 0) / len(segs), 1)
    res = {"run_at": now(), "segments": len(segs),
           "zero_pct": {k: zero(k) for k in ("food_500m", "food_1km", "lodging_1km", "lodging_2km")},
           "median": {k: statistics.median(s[k] for s in segs) for k in ("food_500m", "food_1km", "lodging_1km", "lodging_2km")},
           "latency_p95_s": round(sorted(lat_s)[int(0.95 * (len(lat_s) - 1))], 2), "calls": len(lat_s),
           "food_category_top": cats.most_common(12), "cafe_share_pct": round(100 * cafe / max(food_total, 1), 1),
           "rk17_signal": "0건 구간 50% 초과면 반경 확대 검토", "detail": segs}
    save("p7.json", res)
    print({k: v for k, v in res.items() if k != "detail"})


if __name__ == "__main__":
    main()
