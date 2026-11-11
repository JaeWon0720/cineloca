"""p3 좌표: 행정안전부 주소 검색·좌표 제공 API(DATA-06)로 저장용 좌표 확보율과 좌표계를 확인한다(TBD-04, RK4).

1) 주소가 있는 장소: 도로명주소 → 검색 API(admCd·rnMgtSn·건물번호) → 좌표 API(entX·entY, UTM-K EPSG:5179) → WGS84
2) 주소가 없는 장소: '부산 + 장소명'으로 검색 API를 한 번 시도(건물명 검색). 실패하면 '좌표 없음'(FR-210)
키: JUSO_SEARCH_API_KEY, JUSO_COORD_API_KEY (주소기반산업지원서비스 business.juso.go.kr 승인키)
출력: raw/geocoded.json, results/p3.json
"""
import re
import time
from collections import Counter
from pyproj import Transformer
from common import env, get, load, save, now, norm, DATA

SEARCH = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
COORD = "https://business.juso.go.kr/addrlink/addrCoordApi.do"
UTMK = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
BUSAN = (34.85, 35.45, 128.70, 129.40)  # lat_min, lat_max, lon_min, lon_max


class Busy(RuntimeError):
    pass


def clean_kw(s):
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s or "")
    s = re.sub(r"[%=<>\[\]'\";]", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:40]


def retry(fn, *a):
    for wait in (2, 5, 10, 20, 40, None):
        try:
            return fn(*a)
        except Busy:
            if wait is None:
                raise
            time.sleep(wait)


def search(key, keyword):
    keyword = clean_kw(keyword)
    r = get(SEARCH, params={"confmKey": key, "currentPage": 1, "countPerPage": 5, "keyword": keyword, "resultType": "json"})
    res = r.json().get("results", {})
    code = res.get("common", {}).get("errorCode")
    if code == "E0007":
        raise Busy(code)
    if code not in ("0", None):
        raise RuntimeError(f"검색 API 오류 {code}: {res.get('common', {}).get('errorMessage')}")
    jusos = [j for j in (res.get("juso") or []) if j.get("siNm", "").startswith("부산")]
    return jusos[0] if jusos else None


def coord(key, j):
    r = get(COORD, params={"confmKey": key, "admCd": j["admCd"], "rnMgtSn": j["rnMgtSn"], "udrtYn": j["udrtYn"],
                           "buldMnnm": j["buldMnnm"], "buldSlno": j["buldSlno"], "resultType": "json"})
    res = r.json().get("results", {})
    code = res.get("common", {}).get("errorCode")
    if code == "E0007":
        raise Busy(code)
    if code not in ("0", None):
        raise RuntimeError(f"좌표 API 오류 {code}: {res.get('common', {}).get('errorMessage')}")
    js = res.get("juso") or []
    if not js or not js[0].get("entX"):
        return None
    x, y = float(js[0]["entX"]), float(js[0]["entY"])
    lon, lat = UTMK.transform(x, y)
    return {"entX": x, "entY": y, "lat": round(lat, 6), "lon": round(lon, 6)}


def main():
    skey, ckey = env("JUSO_SEARCH_API_KEY"), env("JUSO_COORD_API_KEY")
    records = load("records.json", DATA)
    if records is None:
        raise SystemExit("p2를 먼저 실행하세요")
    places = {}
    for r in records:
        p = places.setdefault(norm(r["place"]), {"name": r["place"], "address": None})
        p["address"] = p["address"] or r["address"]
    prev = {o["place"]: o for o in (load("geocoded.json", DATA) or []) if o.get("coord")}
    out, stat = [], Counter()
    for k, p in places.items():
        if p["name"] in prev:
            o = prev[p["name"]]
            out.append(o)
            stat["with_address" if p["address"] else "without_address"] += 1
            stat["addr_search_ok" if o["via"] == "address" else "name_search_ok"] += 1
            continue
        item = {"place": p["name"], "address": p["address"], "via": None, "coord": None}
        try:
            j = retry(search, skey, p["address"]) if p["address"] else None
            if j:
                stat["addr_search_ok"] += 1
                item["via"] = "address"
            elif not p["address"]:
                j = retry(search, skey, f"부산 {p['name']}")
                if j:
                    stat["name_search_ok"] += 1
                    item["via"] = "name"
            if j:
                c = retry(coord, ckey, j)
                if c:
                    item["coord"] = c
                    item["road_addr"] = j.get("roadAddr")
        except (RuntimeError, Busy) as e:
            item["error"] = str(e)
            stat["error"] += 1
        stat["with_address" if p["address"] else "without_address"] += 1
        out.append(item)
        time.sleep(0.25)
    save("geocoded.json", out, DATA)
    got = [o for o in out if o["coord"]]
    inb = [o for o in got if BUSAN[0] <= o["coord"]["lat"] <= BUSAN[1] and BUSAN[2] <= o["coord"]["lon"] <= BUSAN[3]]
    n, na = len(out), stat["with_address"]
    res = {"run_at": now(), "places": n, "with_address": na, "without_address": stat["without_address"],
           "address_search_success_pct": round(100 * stat["addr_search_ok"] / max(na, 1), 1),
           "name_search_success_pct": round(100 * stat["name_search_ok"] / max(stat["without_address"], 1), 1),
           "coord_rate_pct": round(100 * len(got) / n, 1),
           "coord_rate_among_address_pct": round(100 * sum(1 for o in got if o["via"] == "address") / max(na, 1), 1),
           "crs": "좌표 API entX·entY = UTM-K(EPSG:5179) → WGS84(EPSG:4326) 변환",
           "in_busan_bbox_pct": round(100 * len(inb) / max(len(got), 1), 1),
           "errors": stat["error"],
           "note": "이름 검색(via=name)으로 얻은 좌표는 건물명 일치라 검수 큐 대상"}
    save("p3.json", res)
    print(res)


if __name__ == "__main__":
    main()
