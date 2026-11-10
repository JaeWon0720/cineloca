"""p1 수집: 부산영상위원회 촬영지 API 2종(DATA-01·02)을 전부 받아 실제 필드를 기록한다.

출력: raw/raw_bfc.json, raw/raw_cinemap.json, results/p1.json, results/mapping.json
키: DATA_GO_KR_SERVICE_KEY(공공데이터포털 일반 인증키, Decoding 값)
"""
from common import env, get, save, now, mask, DATA

SOURCES = {
    "bfc": ("DATA-01 영화 속 부산정보(15164328)", "https://apis.data.go.kr/6260000/BfcMovieBusanService/getBfcMovieBusan"),
    "cinemap": ("DATA-02 씨네맵(15164338)", "https://apis.data.go.kr/6260000/CineMapService/getCineMap"),
}
ROWS = 1000


def find_items(obj):
    """응답 구조가 문서와 조금 달라도 opusNm을 가진 항목 목록과 totalCount를 찾는다."""
    items, total = [], None
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if "totalCount" in o:
                try:
                    total = int(o["totalCount"])
                except (TypeError, ValueError):
                    pass
            if "opusNm" in o:
                items.append(o)
                continue
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return items, total


def collect(url, key):
    out, page, total = [], 1, None
    while True:
        r = get(url, params={"serviceKey": key, "pageNo": page, "numOfRows": ROWS, "resultType": "json"})
        try:
            body = r.json()
        except ValueError:
            raise SystemExit(f"JSON이 아닌 응답({r.status_code}): {mask(r.text[:300])}")
        if "OpenAPI_ServiceResponse" in body:
            raise SystemExit(f"API 오류: {body['OpenAPI_ServiceResponse'].get('cmmMsgHeader')}")
        items, t = find_items(body)
        total = total or t
        out += items
        if not items or (total and len(out) >= total):
            break
        page += 1
    return out, total


def main():
    key = env("DATA_GO_KR_SERVICE_KEY")
    result = {"run_at": now(), "sources": {}}
    mapping = {}
    for sid, (label, url) in SOURCES.items():
        rows, total = collect(url, key)
        save(f"raw_{sid}.json", rows, DATA)
        fields = {}
        for r in rows:
            for k, v in r.items():
                f = fields.setdefault(k, {"non_empty": 0, "example": None})
                if str(v or "").strip():
                    f["non_empty"] += 1
                    f["example"] = f["example"] or v
        result["sources"][sid] = {"label": label, "endpoint": url, "total_count": total, "collected": len(rows),
                                  "fields": {k: {"fill_rate": round(v["non_empty"] / max(len(rows), 1), 3), "example": v["example"]}
                                             for k, v in fields.items()}}
        mapping[sid] = {k: {"opusNm": "work.title", "plcNm": "location.name", "opnDay": "work.release_date",
                            "roadNmAddr": "location.address"}.get(k, "(미사용)") for k in fields}
        print(f"{label}: {len(rows)}건 / totalCount {total} / 필드 {list(fields)}")
    have = set().union(*[set(s["fields"]) for s in result["sources"].values()])
    result["p1_check"] = {"person_genre_coord_fields": sorted(have & {"genre", "actor", "director", "lat", "lng", "lon", "la", "lo", "x", "y"}),
                          "verdict": "인물·장르·좌표 필드 없음 → P1 확인" if not have & {"genre", "actor", "director", "lat", "lng", "lon", "la", "lo", "x", "y"} else "확인 필요"}
    save("p1.json", result)
    save("mapping.json", mapping)
    print(result["p1_check"])


if __name__ == "__main__":
    main()
