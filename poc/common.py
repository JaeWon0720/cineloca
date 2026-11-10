"""착수 PoC 공통: 환경변수(.env), HTTP 재시도, 결과 저장.

키는 저장소 루트의 .env에만 둔다(.gitignore 대상). 결과 JSON에는 키를 남기지 않는다.
"""
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "raw"  # 원자료(KMDb 응답 포함)는 .gitignore의 poc/**/raw/로 커밋 제외
RESULTS = ROOT / "results"
DATA.mkdir(exist_ok=True)
RESULTS.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")
KST = timezone(timedelta(hours=9))


def env(name, required=True):
    v = os.getenv(name, "").strip()
    if required and not v:
        raise SystemExit(f"[키 없음] .env에 {name}을(를) 넣어 주세요 (poc/.env.example 참고)")
    return v


def now():
    return datetime.now(KST).isoformat(timespec="seconds")


def get(url, params=None, headers=None, timeout=15, retries=3, backoff=(2, 5, 10)):
    """GET + 재시도(네트워크 오류·429·5xx). 공공데이터포털은 오류를 200 본문으로 줄 때가 있어 호출부에서 검사한다."""
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504) and i < retries:
                time.sleep(backoff[min(i, len(backoff) - 1)])
                continue
            return r
        except requests.RequestException as e:
            last = e
            if i < retries:
                time.sleep(backoff[min(i, len(backoff) - 1)])
    raise last


def mask(s):
    """로그·결과에 키가 섞이지 않게 serviceKey 등을 가린다."""
    return re.sub(r"((?:serviceKey|ServiceKey|confmKey|key)=)[^&\s]+", r"\1***", str(s))


def save(name, obj, folder=RESULTS):
    p = folder / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    return p


def load(name, folder=RESULTS):
    p = folder / name
    return json.loads(p.read_text()) if p.exists() else None


def norm(s):
    """작품명·장소명 비교용 정규화: 공백·괄호 안 부가설명·문장부호 제거, 소문자."""
    s = (s or "").strip()
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s)
    s = re.sub(r"[\s·,.:;'\"「」『』<>《》!?~\-–—_/]", "", s)
    return s.lower()


DISTRICTS = ["중구", "서구", "동구", "영도구", "부산진구", "동래구", "남구", "북구", "해운대구", "사하구", "금정구", "강서구",
             "연제구", "수영구", "사상구", "기장군"]


def district_of(addr):
    """주소에서 부산 구·군 추출. 부산이 아니면 None."""
    if not addr:
        return None
    a = addr.replace("부산광역시", "부산 ").replace("부산시", "부산 ")
    if "부산" not in a:
        return None
    for d in sorted(DISTRICTS, key=len, reverse=True):
        if re.search(rf"(^|\s){d}(\s|$)", a):
            return d
    return None
