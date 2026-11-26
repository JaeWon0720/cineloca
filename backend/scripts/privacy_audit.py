"""N-08 보안·개인정보 점검 (NFR-003·NFR-011).

1) 위치 요청 100회(API-005 촬영지 검색 · API-016 스탬프 · API-014 코스 계산 · API-013 F3 질문의
   현재 위치, 각 25회)를 보낸 뒤
   DB 전체와 요청 로그·서버 로그에 보낸 좌표가 남았는지 찾는다. F3·F4 질문에 넣은 전화번호·메일
   원문이 남았는지도 함께 찾는다(마스킹본만 남아야 한다).
2) 서명을 바꾼 토큰 100개로 API-017(/stamps/verify)·API-018(/badges)을 불러 모두 거부되는지 본다.
   서명 키는 쓰지 않는다(공격자처럼 서버가 준 토큰만 가지고 바꾼다).

결과는 JSON 요약 한 개로 남긴다. 요약에는 좌표·토큰 원문을 넣지 않는다.

실행 예 (서버를 LOG_DIR을 켜서 띄운 뒤):
    python -m scripts.privacy_audit --base-url http://localhost:8000 \\
        --dsn postgresql://cineloca:cineloca@localhost:5432/cineloca \\
        --log-dir ./logs --log-file uvicorn.out --out privacy_audit.json
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import random
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx

KST = timezone(timedelta(hours=9))
REQUESTS = 100  # 위치 요청 수이자 위조 토큰 수
# 부산 근처 위도·경도처럼 보이는 숫자(소수 4자리 이상). 로그에는 이런 숫자가 하나도 없어야 한다
COORD_LIKE = re.compile(r"(?<![\d.])(3[45]\.\d{4,}|12[89]\.\d{4,})(?![\d])")


# ── 토큰 조작 도구(키 없이) ─────────────────────────────────
def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def split(token: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    h, p, s = token.split(".")
    return json.loads(b64d(h)), json.loads(b64d(p)), s


def encode(header: dict[str, Any], payload: dict[str, Any], sig: str) -> str:
    def part(d: dict[str, Any]) -> str:
        return b64e(json.dumps(d, separators=(",", ":")).encode())

    return f"{part(header)}.{part(payload)}.{sig}"


def hs256(header: dict[str, Any], payload: dict[str, Any], key: bytes) -> str:
    def part(d: dict[str, Any]) -> str:
        return b64e(json.dumps(d, separators=(",", ":")).encode())

    signing = f"{part(header)}.{part(payload)}"
    sig = hmac.new(key, signing.encode(), hashlib.sha256).digest()
    return f"{signing}.{b64e(sig)}"


def flip_last(sig: str) -> str:
    return sig[:-1] + ("A" if sig[-1] != "A" else "B")


def forge_all(stamps: list[str], badges: list[str], rng: random.Random) -> list[tuple[str, str]]:
    """(종류, 토큰) 100개. 원본 서명을 그대로 쓰거나, 서버 키가 아닌 키로 다시 서명한다."""
    out: list[tuple[str, str]] = []
    wrong_key = secrets.token_bytes(40)

    def add(kind: str, token: str) -> None:
        out.append((kind, token))

    for t in stamps[:10]:  # 서명 마지막 글자 바꾸기
        h, p, s = split(t)
        add("sig_flip_last", encode(h, p, flip_last(s)))
    for t in stamps[:10]:  # 서명 무작위로 바꾸기
        h, p, s = split(t)
        add("sig_random", encode(h, p, b64e(secrets.token_bytes(32))))
    for t in stamps[:10]:  # 촬영지 번호 바꾸고 원래 서명
        h, p, s = split(t)
        add("payload_loc_old_sig", encode(h, {**p, "loc": p["loc"] + 1}, s))
    for t in stamps[:5]:  # 인증 시각 바꾸고 원래 서명
        h, p, s = split(t)
        add("payload_iat_old_sig", encode(h, {**p, "iat": p["iat"] - 86400}, s))
    for t in stamps[:10]:  # 다른 키로 다시 서명(kid는 서버 것 그대로)
        h, p, _ = split(t)
        add("wrong_key_same_kid", hs256(h, p, wrong_key))
    for t in stamps[:5]:  # 다른 키·다른 kid
        h, p, _ = split(t)
        add("wrong_key_wrong_kid", hs256(h, {**p, "kid": secrets.token_hex(4)}, wrong_key))
    for t in stamps[:5]:  # alg none
        h, p, _ = split(t)
        add("alg_none", encode({**h, "alg": "none"}, p, ""))
    for t in stamps[:5]:  # 빈 키로 서명
        h, p, _ = split(t)
        add("empty_key", hs256(h, p, b""))
    for t in stamps[:5]:  # 필드 추가하고 원래 서명
        h, p, s = split(t)
        add("extra_claim_old_sig", encode(h, {**p, "admin": True}, s))
    for i in range(10):  # 배지 코스 키 바꾸고 원래 서명
        h, p, s = split(badges[i % len(badges)])
        add("badge_course_key_old_sig", encode(h, {**p, "course_key": f"course:{9000 + i}"}, s))
    for t in stamps[:5]:  # 스탬프를 배지로 바꾸고 원래 서명
        h, p, s = split(t)
        body = {"typ": "badge", "course_key": "course:1", "iat": p["iat"], "kid": p["kid"]}
        add("typ_swap_old_sig", encode(h, body, s))
    for t in stamps[:5]:  # 서명 잘라 내기
        h, p, s = split(t)
        add("sig_truncated", encode(h, p, s[: len(s) // 2]))
    for t in stamps[:5]:  # 서명 지우기
        h, p, _ = split(t)
        add("sig_removed", encode(h, p, ""))
    for t in stamps[:5]:  # kid만 바꾸고 원래 서명
        h, p, s = split(t)
        add("kid_changed_old_sig", encode(h, {**p, "kid": "00000000"}, s))
    garbage = [
        "",
        "not-a-token",
        "a.b.c",
        stamps[0] + "." + stamps[0],
        stamps[0] + "A" * 1200,  # 1,000자 넘음
    ]
    for g in garbage:
        add("garbage", g)
    rng.shuffle(out)
    assert len(out) == REQUESTS, len(out)
    return out


# ── DB 스냅샷 ───────────────────────────────────────────────
async def db_snapshot(dsn: str) -> dict[str, set[str]]:
    conn = await asyncpg.connect(dsn)
    try:
        tables = [
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
            )
        ]
        snap: dict[str, set[str]] = {}
        for t in tables:
            rows = await conn.fetch(f'SELECT row_to_json(x)::text AS j FROM public."{t}" x')
            snap[t] = {r["j"] for r in rows}
        return snap
    finally:
        await conn.close()


# ── 위치 요청 100회 ─────────────────────────────────────────
@dataclass
class Sent:
    coords: list[tuple[float, float]] = field(default_factory=list)
    by_api: dict[str, dict[str, int]] = field(default_factory=dict)

    def note(self, api: str, outcome: str) -> None:
        self.by_api.setdefault(api, {})
        self.by_api[api][outcome] = self.by_api[api].get(outcome, 0) + 1


def jitter(rng: random.Random) -> float:
    """다른 데이터와 겹치지 않게 소수 7~8자리까지 있는 작은 값."""
    return rng.randint(1, 9) * 1e-5 + rng.randint(1, 999) * 1e-8


def candidates(client: httpx.Client, want: int) -> list[dict[str, Any]]:
    """인증할 수 있는 촬영지(좌표는 서버가 준 값). 이 단계에서는 좌표를 보내지 않는다."""
    found: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()
    for loc_id in range(1, 400):
        r = client.get(f"/api/locations/{loc_id}")
        if r.status_code != 200:
            continue
        d = r.json()
        if not d.get("cert", {}).get("eligible") or (d["lat"], d["lon"]) in seen:
            continue
        seen.add((d["lat"], d["lon"]))
        found.append({"id": d["id"], "lat": d["lat"], "lon": d["lon"]})
        if len(found) >= want:
            break
    return found


def send_location_requests(
    client: httpx.Client, locs: list[dict[str, Any]], rng: random.Random, notice: str
) -> tuple[Sent, dict[int, str]]:
    """API 4개에 25회씩. F3 질문에는 개인정보(전화·메일)를 넣어 원문이 남는지도 본다."""
    sent = Sent()
    tokens: dict[int, str] = {}

    def coord(lat: float, lon: float) -> tuple[float, float]:
        c = (round(lat + jitter(rng), 8), round(lon + jitter(rng), 8))
        sent.coords.append(c)
        return c

    # API-005 촬영지 검색 25회(그중 3회는 잘못된 반경 → 422)
    for i in range(25):
        base = locs[i % len(locs)]
        lat, lon = coord(base["lat"], base["lon"])
        radius = 1000 if i < 22 else 777
        r = client.post(
            "/api/locations/search", json={"base": {"lat": lat, "lon": lon}, "radius_m": radius}
        )
        sent.note("API-005 POST /locations/search", str(r.status_code))

    # API-016 스탬프 25회: 반경 안 12 · 같은 곳 다시 3 · 반경 밖 5 · 정확도 나쁨 3 · 형식 오류 2
    plan = ["in"] * 12 + ["again"] * 3 + ["out"] * 5 + ["low_acc"] * 3 + ["invalid"] * 2
    for i, kind in enumerate(plan):
        # 다시 인증은 앞에서 인증한 곳(토큰을 held로 보냄)
        loc = locs[i % 12] if kind == "again" else locs[i % len(locs)]
        if kind == "out":
            lat, lon = coord(loc["lat"] + 0.01, loc["lon"] + 0.01)
        else:
            lat, lon = coord(loc["lat"], loc["lon"])
        body: dict[str, Any] = {
            "location_id": loc["id"],
            "lat": lat,
            "lon": lon,
            "accuracy_m": 120.0 if kind == "low_acc" else 15.0,
            "held": [tokens[loc["id"]]] if kind == "again" and loc["id"] in tokens else [],
        }
        if kind == "invalid":
            body["accuracy_m"] = -1  # 422: 본문 검사 오류에 좌표가 실려 나가는지
        r = client.post("/api/stamps", json=body)
        outcome = str(r.status_code)
        if r.status_code == 200:
            d = r.json()
            outcome = "ok" if d.get("ok") else str(d.get("reason"))
            if d.get("ok"):
                tokens[loc["id"]] = d["token"]
        sent.note("API-016 POST /stamps", outcome)

    # API-014 코스 계산(출발지 = 현재 위치) 25회
    for i in range(25):
        a, b = locs[i % len(locs)], locs[(i + 1) % len(locs)]
        lat, lon = coord(a["lat"] + 0.002, a["lon"] + 0.002)
        r = client.post(
            "/api/courses/plan",
            json={
                "start": {"type": "current", "lat": lat, "lon": lon},
                "location_ids": [a["id"], b["id"]],
                "fixed_order": False,
            },
        )
        outcome = str(r.status_code)
        if r.status_code == 200 and r.json().get("start", {}).get("lat") is not None:
            outcome = "200_echoed_start_coords"  # 응답에 현재 위치를 되돌려 주면 결함
        sent.note("API-014 POST /courses/plan", outcome)

    # API-013 F3 AI 탐색('내 위치' 기준) 25회. 질문에 전화번호·메일을 넣는다
    headers = {"X-Notice-Version": notice}
    for i in range(25):
        loc = locs[i % len(locs)]
        lat, lon = coord(loc["lat"] - 0.001, loc["lon"] - 0.001)
        phone, mail = pii(rng)
        text = f"내 위치 근처 영화 촬영지 알려 줘 연락처 {phone} {mail}"
        r = client.post(
            "/api/query", headers=headers, json={"text": text, "base": {"lat": lat, "lon": lon}}
        )
        sent.note("API-013 POST /query", str(r.status_code))

    # API-015 F4 시설 조건(좌표 없음) 10회: 조건 원문에 개인정보
    for i in range(10):
        a, b = locs[i % len(locs)], locs[(i + 1) % len(locs)]
        phone, mail = pii(rng)
        r = client.post(
            "/api/courses/amenities",
            headers=headers,
            json={"location_ids": [a["id"], b["id"]], "text": f"카페 {phone} {mail}"},
        )
        sent.note("API-015 POST /courses/amenities (원문만)", str(r.status_code))

    assert len(sent.coords) == REQUESTS, len(sent.coords)
    return sent, tokens


PII_SENT: list[str] = []


def pii(rng: random.Random) -> tuple[str, str]:
    """다른 데이터와 겹치지 않는 가짜 전화번호·메일. 찾을 모양도 함께 모아 둔다."""
    a, b = rng.randint(1000, 9999), rng.randint(1000, 9999)
    phone = f"010-{a}-{b}"
    mail = f"audit{rng.randint(10**7, 10**8 - 1)}@example.com"
    PII_SENT.extend([phone, f"010{a}{b}", f"{a}-{b}", mail, mail.split("@")[0]])
    return phone, mail


def coord_strings(c: tuple[float, float]) -> list[str]:
    """보낸 좌표가 적힐 수 있는 모양: 그대로, 소수 6자리, 소수 5자리."""
    out = []
    for v in c:
        out += [repr(v), f"{v:.6f}", f"{v:.5f}"]
    return out


def search_text(text: str, needles: list[str]) -> int:
    return sum(1 for n in needles if n in text)


# ── 토큰 위조 시험 ──────────────────────────────────────────
def token_tests(client: httpx.Client, tokens: dict[int, str], rng: random.Random) -> dict[str, Any]:
    ids = list(tokens)
    if len(ids) < 2:
        raise SystemExit("반경 안 스탬프를 2개 이상 받지 못했습니다(촬영지 데이터 확인)")
    stamps = list(tokens.values())
    # 대조군: 진짜 토큰은 통과해야 시험이 의미가 있다
    control_verify = client.post("/api/stamps/verify", json={"tokens": stamps}).json()
    pair = ids[:2]
    badge = client.post(
        "/api/badges", json={"course": {"location_ids": pair}, "stamps": [tokens[i] for i in pair]}
    ).json()
    if not badge.get("ok"):
        raise SystemExit("대조군 배지를 받지 못했습니다")
    badges = [badge["token"]]
    for i in range(2, min(len(ids), 6)):
        b = client.post(
            "/api/badges",
            json={
                "course": {"location_ids": [ids[0], ids[i]]},
                "stamps": [tokens[ids[0]], tokens[ids[i]]],
            },
        ).json()
        if b.get("ok"):
            badges.append(b["token"])
    control_badge_verify = client.post("/api/stamps/verify", json={"tokens": badges}).json()

    forged = forge_all(stamps, badges, rng)
    api017 = {"calls": 0, "rejected": 0, "accepted": 0, "http_errors": 0, "by_kind": {}}
    api018 = {"calls": 0, "rejected": 0, "accepted": 0, "http_errors": 0, "by_kind": {}}
    first, second = pair
    for kind, tok in forged:
        # API-017: 위조 토큰 하나만 보냄 → valid·badges 비고 invalid_count = 1
        r = client.post("/api/stamps/verify", json={"tokens": [tok]})
        api017["calls"] += 1
        k = api017["by_kind"].setdefault(kind, {"sent": 0, "rejected": 0})
        k["sent"] += 1
        if r.status_code == 200:
            d = r.json()
            ok_reject = d["valid"] == [] and d["badges"] == [] and d["invalid_count"] == 1
        else:
            api017["http_errors"] += 1
            ok_reject = r.status_code in (400, 422)  # 형식 오류로 거부해도 거부
        api017["rejected" if ok_reject else "accepted"] += 1
        k["rejected"] += ok_reject

        # API-018: 진짜 스탬프 1개 + 위조 토큰 1개 → 위조 쪽 장소가 missing에 남아야 함
        r = client.post(
            "/api/badges",
            json={"course": {"location_ids": [first, second]}, "stamps": [tokens[first], tok]},
        )
        api018["calls"] += 1
        k = api018["by_kind"].setdefault(kind, {"sent": 0, "rejected": 0})
        k["sent"] += 1
        if r.status_code == 200:
            d = r.json()
            ok_reject = (
                d["ok"] is False
                and d["token"] is None
                and d["reason"] == "missing_stamps"
                and d["missing"] == [second]
            )
        else:
            api018["http_errors"] += 1
            ok_reject = r.status_code in (400, 422)
        api018["rejected" if ok_reject else "accepted"] += 1
        k["rejected"] += ok_reject

    # 한 번에 100개
    batch = client.post("/api/stamps/verify", json={"tokens": [t for _, t in forged]}).json()
    return {
        "control": {
            "real_stamps_sent": len(stamps),
            "real_stamps_valid": len(control_verify["valid"]),
            "real_badge_issued": True,
            "real_badges_sent": len(badges),
            "real_badges_valid": len(control_badge_verify["badges"]),
        },
        "forged_tokens": len(forged),
        "API-017 POST /stamps/verify (1개씩)": api017,
        "API-017 POST /stamps/verify (100개 한 번에)": {
            "valid": len(batch["valid"]),
            "badges": len(batch["badges"]),
            "invalid_count": batch["invalid_count"],
        },
        "API-018 POST /badges (진짜 1 + 위조 1)": api018,
    }


# ── 실행 ───────────────────────────────────────────────────
def read_logs(dirs: list[str], files: list[str]) -> dict[str, str]:
    texts: dict[str, str] = {}
    for d in dirs:
        for p in sorted(Path(d).glob("*")):
            if p.is_file():
                texts[str(p)] = p.read_text(encoding="utf-8", errors="replace")
    for f in files:
        p = Path(f)
        if p.is_file():
            texts[str(p)] = p.read_text(encoding="utf-8", errors="replace")
    return texts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--log-dir", action="append", default=[], help="요청 로그 폴더(LOG_DIR)")
    ap.add_argument("--log-file", action="append", default=[], help="서버 콘솔·DB 서버 로그 파일")
    ap.add_argument("--out", default="privacy_audit.json")
    ap.add_argument("--seed", type=int, default=20261126)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    before = asyncio.run(db_snapshot(args.dsn))
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        locs = candidates(client, 15)
        notice = client.get("/api/sources").json()["notice_version"]
        sent, tokens = send_location_requests(client, locs, rng, notice)
        tokens_result = token_tests(client, tokens, rng)
    time.sleep(1)  # 로그가 파일에 다 쓰이도록
    after = asyncio.run(db_snapshot(args.dsn))

    needles = [s for c in sent.coords for s in coord_strings(c)]
    exact = [repr(v) for c in sent.coords for v in c]
    added = {t: after.get(t, set()) - before.get(t, set()) for t in after}
    removed = {t: before.get(t, set()) - after.get(t, set()) for t in before}
    db_all_text = "\n".join("\n".join(rows) for rows in after.values())
    # DB 전체에서는 보낸 값 그대로(소수 7~8자리)를 찾는다. 소수 5·6자리로 줄인 모양은
    # 원래 있던 촬영지·역 좌표와 우연히 겹치므로, 점검 중에 새로 생기거나 바뀐 행에서만 찾는다
    db_hits = {t: search_text("\n".join(rows), exact + PII_SENT) for t, rows in after.items()}
    added_hits = {t: search_text("\n".join(rows), needles) for t, rows in added.items()}

    logs = read_logs(args.log_dir, args.log_file)
    log_result = {}
    for path, text in logs.items():
        log_result[path] = {
            "bytes": len(text.encode()),
            "lines": text.count("\n"),
            "sent_coord_hits": search_text(text, needles),
            "pii_original_hits": search_text(text, PII_SENT),
            "masked_query_lines": sum(
                1 for line in text.splitlines() if '"query"' in line or "[전화번호]" in line
            ),
            "coord_like_numbers": len(COORD_LIKE.findall(text)),
        }

    coords_in_db = sum(db_hits.values()) + sum(added_hits.values())
    coords_in_logs = sum(
        v["sent_coord_hits"] + v["coord_like_numbers"] + v["pii_original_hits"]
        for v in log_result.values()
    )
    t = tokens_result
    tokens_pass = (
        t["control"]["real_stamps_valid"] == t["control"]["real_stamps_sent"]
        and t["control"]["real_badges_valid"] == t["control"]["real_badges_sent"]
        and t["API-017 POST /stamps/verify (1개씩)"]["rejected"] == REQUESTS
        and t["API-018 POST /badges (진짜 1 + 위조 1)"]["rejected"] == REQUESTS
        and t["API-017 POST /stamps/verify (100개 한 번에)"]["invalid_count"] == REQUESTS
        and t["API-017 POST /stamps/verify (100개 한 번에)"]["valid"] == 0
        and t["API-017 POST /stamps/verify (100개 한 번에)"]["badges"] == 0
    )
    echoed = sent.by_api.get("API-014 POST /courses/plan", {}).get("200_echoed_start_coords", 0)
    summary = {
        "test": "N-08 보안·개인정보 (NFR-003·NFR-011)",
        "run_at": datetime.now(KST).isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "location_requests": {
            "sent": len(sent.coords),
            "by_api": sent.by_api,
            "search_forms": (
                "로그: 보낸 위도·경도의 원래 값·소수 6자리·소수 5자리"
                " + 부산 좌표처럼 보이는 숫자 전부 + 질문에 넣은 전화번호·메일 원문."
                " DB: 전체 테이블에서 좌표 원래 값·전화번호·메일, 점검 중 바뀐 행에서 좌표 세 모양"
            ),
            "needles": len(needles),
            "pii_needles": len(PII_SENT),
            "db": {
                "tables": len(after),
                "rows_added": {k: len(v) for k, v in added.items() if v},
                "rows_removed": {k: len(v) for k, v in removed.items() if v},
                "sent_coord_hits": coords_in_db,
                "exact_hits_by_table": {k: v for k, v in db_hits.items() if v},
                "changed_row_hits_by_table": {k: v for k, v in added_hits.items() if v},
                "db_text_bytes": len(db_all_text.encode()),
            },
            "logs": log_result,
            "start_coords_echoed_in_plan_response": echoed,
            "pass": coords_in_db == 0 and coords_in_logs == 0 and echoed == 0 and bool(logs),
        },
        "tampered_tokens": {**tokens_result, "pass": tokens_pass},
    }
    summary["pass"] = summary["location_requests"]["pass"] and tokens_pass
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
