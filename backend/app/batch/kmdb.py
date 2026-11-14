"""KMDb 매칭 (FR-603·604, BR-11, TBD-03). PoC p4_match.py·p4b_noyear.py의 규칙을 옮기고
v2로 보완했다.

점수 = 0.75 × 제목 유사도(정규화 후 rapidfuzz ratio) + 0.25 × 연도 점수
  연도 점수: 개봉연도 같음 1, ±1년 0.5, 모름 0.5, 그 밖 0
  (KMDb 개봉일이 없으면 제작연도로, 제작연도는 개봉 전해 허용)
  0.9 이상 자동 연결 / 0.6 이상 검수 대기 / 미만 미매칭

v2 보완(2026-11-13, 자동 연결 표본에서 찾은 오류 유형 기준):
  1) 방송사·OTT 머리말('JTBC ', '넷플릭스 ' 등)은 떼고 찾는다. 작품 이름(title_norm)은 그대로 둔다.
  2) 동명 작품: 1등과 0.05 안에 다른 후보가 있으면 근거 순서(개봉일 연도 일치 > 극장용·개봉일 있음 >
     60분 이상)로 고르고, 그래도 같으면 자동 연결하지 않고 검수로 보낸다.
  3) 개봉연도를 모르는 작품은 1998년 전 후보를 뺀다(원본 개봉연도는 모두 2000년 이후).
  4) 규칙 U: 개봉연도를 모를 때 제목이 정규화 후 완전히 같고 단편(40분 미만)이 아닌 후보가
     하나뿐이면 자동 연결. 머리말이 없는 작품은 극장용이거나 개봉일이 있어야 하고, 머리말이 있는
     작품은 TV·인터넷용이거나 한국 작품이어야 한다(방송사 머리말이면 극장 개봉작 제외).
규칙 C(개봉일 없는 작품): 제목(또는 다른 제목)이 완전히 같고 KMDb 촬영 장소(fLocation)나
  줄거리·키워드에 '부산' 또는 우리 촬영지 이름이 있는 후보가 정확히 하나면 자동 연결.
관리자 검수 결과(FR-611)는 자동 판정보다 우선한다.

후보는 KMDb 오픈API(KMDB_SERVICE_KEY)로 받는다.
키가 없을 때는 KMDb 오픈자료 '영화기본정보' CSV(KMDB_CSV_PATH)로 대신한다.
CSV에는 촬영 장소가 없어 규칙 C는 줄거리·키워드로만 판단한다.
"""

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

import httpx
from rapidfuzz import fuzz, process

from app.batch.merge import norm, year_of

AUTO, REVIEW = 0.9, 0.6
TIE_GAP = 0.05
MIN_YEAR_UNKNOWN = 1998
SHORT_MIN = 40
API_URL = "http://api.koreafilm.or.kr/openapi-data2/wisenut/search_api/search_json2.jsp"
DETAIL_URL = "https://www.kmdb.or.kr/db/kor/detail/movie/{kind}/{no}"
_HL = re.compile(r"\s*!H[SE]\s*")
TV_PREFIXES = ("JTBC", "KBS", "MBC", "SBS", "tvN", "OCN", "ENA", "EBS", "TV조선", "MBN", "채널A")
OTT_PREFIXES = (
    "넷플릭스", "디즈니+", "티빙", "왓챠", "쿠팡플레이", "카카오TV", "애플TV+", "아마존 프라임",
    "웨이브", "시즌",
)  # fmt: skip
MEDIA_PREFIXES = TV_PREFIXES + OTT_PREFIXES
_MEDIA = re.compile(r"^(" + "|".join(re.escape(m) for m in MEDIA_PREFIXES) + r")\s+(.+)$")


@dataclass(frozen=True)
class Candidate:
    docid: str
    title: str
    prod_year: str | None = None
    rel_date: str | None = None
    type: str | None = None
    f_location: str | None = None
    title_etc: str | None = None
    plot: str | None = None
    keywords: str | None = None
    link: str | None = None
    use: str | None = None  # 용도: 극장용·TV용·OTT 등
    runtime: int | None = None  # 분
    director: str | None = None
    country: str | None = None


class CandidateSource(Protocol):
    has_location: bool

    async def candidates(self, title: str) -> list[Candidate]: ...


def split_media(title: str) -> tuple[str, str | None]:
    """'JTBC 나의 해방일지' → ('나의 해방일지', 'JTBC'). 머리말이 없으면 (title, None)."""
    m = _MEDIA.match(" ".join(title.split()))
    return (m.group(2), m.group(1)) if m else (title, None)


def cand_year(c: Candidate) -> int | None:
    return year_of(c.rel_date) or year_of(c.prod_year)


def score(work_title: str, rel_year: int | None, c: Candidate) -> float:
    t = fuzz.ratio(norm(work_title), norm(c.title)) / 100
    ry, py = year_of(c.rel_date), year_of(c.prod_year)
    if rel_year is None or (ry is None and py is None):
        y = 0.5
    elif ry is not None:
        y = 1.0 if ry == rel_year else 0.5 if abs(ry - rel_year) == 1 else 0.0
    else:
        y = 1.0 if py in (rel_year, rel_year - 1) else 0.5 if abs(py - rel_year) <= 2 else 0.0
    return round(0.75 * t + 0.25 * y, 3)


def _exact(title: str, c: Candidate) -> bool:
    t = norm(title)
    return norm(c.title) == t or any(norm(x) == t for x in (c.title_etc or "").split("^") if x)


def _busan_evidence(c: Candidate, place_keys: set[str]) -> bool:
    fl = c.f_location or ""
    if "부산" in fl or {norm(x) for x in fl.split(",") if x.strip()} & place_keys:
        return True
    text = norm(f"{c.plot or ''} {c.keywords or ''}")
    return "부산" in text or any(len(p) >= 3 and p in text for p in place_keys)


def _theatrical(c: Candidate) -> bool:
    return (c.use or "") == "극장용" or year_of(c.rel_date) is not None


def _short(c: Candidate) -> bool:
    return c.runtime is not None and c.runtime < SHORT_MIN


def _evidence(rel_year: int | None, c: Candidate) -> tuple[int, int, int]:
    """동명 후보를 가를 근거. 클수록 강하다."""
    return (
        int(rel_year is not None and year_of(c.rel_date) == rel_year),
        int(_theatrical(c)),
        int(c.runtime is not None and c.runtime >= 60),
    )


def rule_c(title: str, cands: Iterable[Candidate], place_keys: set[str]) -> Candidate | None:
    ex = [c for c in cands if _exact(title, c) and _busan_evidence(c, place_keys)]
    return ex[0] if len(ex) == 1 else None


def _media_fits(c: Candidate, media: str) -> bool:
    """방송사·OTT 작품에 붙일 수 있는 후보인가. 같은 제목의 외국 극장 영화를 막는다."""
    if (c.use or "") in ("TV용", "인터넷용"):
        return True
    if media in TV_PREFIXES and _theatrical(c):  # 방송 드라마에 극장 개봉작은 붙이지 않는다
        return False
    return "대한민국" in (c.country or "")


def rule_u(title: str, cands: list[Candidate], media: str | None) -> Candidate | None:
    """제목이 완전히 같은 후보 중 단편이 아니고 매체가 맞는 후보가 하나뿐이면 그 후보.

    다른 동명 후보(단편 등)를 제쳤을 때는 남은 후보에 개봉일이 있거나, 방송사·OTT 작품이면
    TV·인터넷용이어야 한다(특근 사례: 개봉일 없는 41분 작품이 36분 작품을 제치고 혼자 남는 것을
    막음).
    """
    ex = [c for c in cands if _exact(title, c)]
    fits = [c for c in ex if not _short(c) and (_media_fits(c, media) if media else _theatrical(c))]
    if len(fits) != 1:
        return None
    c = fits[0]
    strong = year_of(c.rel_date) is not None or (media and c.use in ("TV용", "인터넷용"))
    return c if len(ex) == 1 or strong else None


@dataclass(frozen=True)
class MatchResult:
    status: str  # matched | review | unmatched (스키마 BR-11 값)
    rule: str  # score | rule_c | rule_u | tie | none
    score: float
    best: Candidate | None


def decide(
    title: str, rel_year: int | None, cands: list[Candidate], place_keys: set[str]
) -> MatchResult:
    title, media = split_media(title)
    if rel_year is None:
        cands = [c for c in cands if (cand_year(c) or MIN_YEAR_UNKNOWN) >= MIN_YEAR_UNKNOWN]
    scored = sorted(((score(title, rel_year, c), c) for c in cands), key=lambda sc: -sc[0])
    best_s, best = scored[0] if scored else (0.0, None)
    top = [c for s, c in scored if s >= best_s - TIE_GAP]
    tied = False
    if len(top) > 1:
        top.sort(key=lambda c: _evidence(rel_year, c), reverse=True)
        tied = _evidence(rel_year, top[0]) == _evidence(rel_year, top[1])
        best = top[0]
        best_s = score(title, rel_year, best)
    if best_s >= AUTO and not tied:
        return MatchResult("matched", "score", best_s, best)
    if rel_year is None:
        if c := rule_c(title, cands, place_keys):
            return MatchResult("matched", "rule_c", score(title, rel_year, c), c)
        if c := rule_u(title, cands, media):
            return MatchResult("matched", "rule_u", score(title, rel_year, c), c)
    if best_s >= REVIEW:
        return MatchResult("review", "tie" if tied else "score", best_s, best)
    return MatchResult("unmatched", "none", best_s, best)


def stored_kmdb(c: Candidate) -> dict:
    """work.kmdb에 넣는 값(DATA-04 STORE일 때만). 이미지 주소·파일은 넣지 않는다."""
    return {"plot": c.plot, "keywords": c.keywords, "link": c.link, "source_id": "DATA-04"}


def _int(s) -> int | None:
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def _director(x: dict) -> str | None:
    ds = (x.get("directors") or {}).get("director") or []
    return ", ".join(d.get("directorNm", "") for d in ds if d.get("directorNm")) or None


class KmdbApi:
    has_location = True

    def __init__(self, http: httpx.AsyncClient, key: str):
        self.http, self.key = http, key

    async def candidates(self, title: str) -> list[Candidate]:
        params = {
            "collection": "kmdb_new2",
            "ServiceKey": self.key,
            "detail": "Y",
            "query": title,
            "listCount": 30,
        }
        r = await self.http.get(API_URL, params=params, timeout=15)
        data = (r.json().get("Data") or [{}])[0]
        out = []
        for x in data.get("Result") or []:
            plots = (x.get("plots") or {}).get("plot") or [{}]
            out.append(
                Candidate(
                    docid=x.get("DOCID"),
                    title=_HL.sub("", x.get("title") or "").strip(),
                    prod_year=x.get("prodYear"),
                    rel_date=x.get("repRlsDate"),
                    type=x.get("type"),
                    f_location=x.get("fLocation"),
                    title_etc=x.get("titleEtc"),
                    plot=(plots[0] or {}).get("plotText"),
                    keywords=x.get("keywords"),
                    link=x.get("kmdbUrl"),
                    use=x.get("use"),
                    runtime=_int(x.get("runtime")),
                    director=_director(x),
                    country=x.get("nation"),
                )
            )
        return out


class KmdbCsv:
    """KMDb 오픈자료 '영화기본정보' CSV(CP949).

    헤더 25칸, 데이터 26칸(출연이 두 번)이라 위치로 읽는다."""

    has_location = False
    # 0 ID종류 1 번호 2 영화명 3 영문제명 4 원제명 5 유형 6 용도 8 제작국가 9 제작년도
    # 11 감독 19 개봉일 20 상영시간 21 키워드 22 줄거리
    I_KIND, I_NO, I_TITLE, I_EN, I_ORG, I_TYPE, I_USE = 0, 1, 2, 3, 4, 5, 6
    I_NAT, I_YEAR, I_DIR, I_REL, I_RUN, I_KW, I_PLOT = 8, 9, 11, 19, 20, 21, 22

    def __init__(self, rows: list[Candidate]):
        self.rows = rows
        self.keys = [norm(c.title) for c in rows]
        self.exact: dict[str, list[int]] = {}
        for i, k in enumerate(self.keys):
            self.exact.setdefault(k, []).append(i)

    @classmethod
    def load(cls, path: str) -> "KmdbCsv":
        seen, rows = set(), []
        with open(path, encoding="cp949", errors="replace", newline="") as f:
            for i, r in enumerate(csv.reader(f)):
                if i == 0 or len(r) < 26:
                    continue
                docid = f"{r[cls.I_KIND]}{r[cls.I_NO]}"
                if docid in seen:
                    continue
                seen.add(docid)
                v = [None if s.strip() in ("", "null") else s.strip() for s in r]
                rows.append(
                    Candidate(
                        docid=docid,
                        title=r[cls.I_TITLE].strip(),
                        prod_year=v[cls.I_YEAR],
                        rel_date=v[cls.I_REL],
                        type=v[cls.I_TYPE],
                        plot=v[cls.I_PLOT],
                        keywords=v[cls.I_KW],
                        use=v[cls.I_USE],
                        runtime=_int(v[cls.I_RUN]),
                        director=v[cls.I_DIR],
                        country=v[cls.I_NAT],
                        title_etc="^".join(x for x in (v[cls.I_EN], v[cls.I_ORG]) if x) or None,
                        link=DETAIL_URL.format(kind=r[cls.I_KIND], no=r[cls.I_NO]),
                    )
                )
        return cls(rows)

    async def candidates(self, title: str) -> list[Candidate]:
        """제목이 완전히 같은 후보는 모두, 그 밖은 비슷한 순으로 10개(동명 작품을 놓치지 않게)."""
        key = norm(title)
        idx = list(self.exact.get(key, []))
        hits = process.extract(key, self.keys, scorer=fuzz.ratio, limit=10, score_cutoff=50)
        idx += [i for _, _, i in hits if i not in idx]
        return [self.rows[i] for i in idx]
