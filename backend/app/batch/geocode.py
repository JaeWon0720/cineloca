"""촬영지 주소·장소명 → 좌표 (FR-605, #25).

1) 번지·건물번호까지 있는 주소(정확한 주소)는 행정안전부 도로명주소 검색 API
   → 좌표제공 API(UTM-K EPSG:5179) → WGS84. 검색이 안 되면 번지 뒤 설명을 잘라 한 번 더.
2) 주소가 없거나 동까지만 있는 주소는 주소 API를 쓰지 않는다(#25: 엉뚱한 건물 좌표가 나옴).
   대신 카카오 로컬 키워드 검색(장소명 + 구·동)으로 찾고,
   - 결과의 구·동이 원본(주소 또는 장소명 괄호의 동)과 다르면 버리고,
   - 이름이 같은 결과가 서로 멀리 떨어져 여럿이면(동명 장소) 버린다.
   카카오 응답은 저장하지 않고 좌표만 쓴다(BR-01). SDD ADR-006과 다른 점은 #25 결정에 따른다.
부산 범위 밖 좌표는 버린다. E0007(과다 요청)은 잠깐 쉬고 다시 부른다.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from pyproj import Transformer

from app.batch.merge import district_of, norm
from app.batch.stations import distance_m

DEFAULT_BASE = "https://business.juso.go.kr/addrlink"
KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
BUSAN_BBOX = (34.85, 35.45, 128.70, 129.40)  # lat_min, lat_max, lon_min, lon_max
BUSY_WAITS = (2, 5, 10, 20)
SAME_PLACE_M = 500  # 이름이 같은 결과가 이보다 멀리 흩어져 있으면 동명 장소로 본다
_UTMK = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
_PAREN = re.compile(r"\(.*?\)|\[.*?\]")
_FORBIDDEN = re.compile(r"[%=<>\[\]'\";]")
# '… 센텀5로 55 1층 ○○' → '… 센텀5로 55', '… 기룡리 630 ○○' → '… 기룡리 630'
_TAIL = re.compile(r"^(.*?(?:로|길)\s*\d+(?:-\d+)?|.*?(?:동|리|가)\s*(?:산\s*)?\d+(?:-\d+)?)\s.+$")
_DONG = re.compile(r"^([가-힣]+?)\d*(동|읍|면|리)(?:\d*가)?$")
_REGION = re.compile(r"^[가-힣]{2,5}$")


class Busy(RuntimeError):
    """E0007: 잠시 후 다시."""


class JusoError(RuntimeError):
    pass


def clean_keyword(s: str | None) -> str:
    s = _FORBIDDEN.sub(" ", _PAREN.sub(" ", s or ""))
    return " ".join(s.split())[:40]


def trim_tail(keyword: str) -> str | None:
    m = _TAIL.match(keyword)
    return m.group(1) if m else None


def precise(address: str | None) -> bool:
    """괄호 밖에 숫자(번지·건물번호)가 있는 주소만 주소 API로 찾는다(#25)."""
    return bool(address) and bool(re.search(r"\d", _PAREN.sub("", address)))


def dong_key(word: str) -> str | None:
    """'우1동'→'우동', '중앙동3가'→'중앙동'처럼 숫자를 뺀 법정동 형태. 동·읍·면·리가 아니면 None."""
    m = _DONG.match(word)
    return m.group(1) + m.group(2) if m else None


@dataclass(frozen=True)
class Hints:
    gu: str | None = None  # 주소의 구·군
    dong: str | None = None  # 주소·장소명의 동('(온천동)', '우1동 치안센터')
    area: str | None = None  # 장소명 괄호의 동 아닌 지역 이름('(정관)', '(양산)')


def hints(name: str, address: str | None) -> Hints:
    """원본에서 결과를 걸러 낼 구·동·지역을 뽑는다."""
    paren = [w.strip() for w in re.findall(r"\(([^)]*)\)", name)]
    words = (address or "").split() + paren + _PAREN.sub(" ", name).split()
    dong = next((d for w in words if (d := dong_key(w))), None)
    area = next((w for w in paren if _REGION.match(w) and not dong_key(w)), None)
    return Hints(district_of(address), dong, area)


def utmk_to_wgs84(x: float, y: float) -> tuple[float, float]:
    lon, lat = _UTMK.transform(x, y)
    return round(lat, 6), round(lon, 6)


def in_busan(lat: float, lon: float) -> bool:
    return BUSAN_BBOX[0] <= lat <= BUSAN_BBOX[1] and BUSAN_BBOX[2] <= lon <= BUSAN_BBOX[3]


@dataclass(frozen=True)
class GeoResult:
    lat: float
    lon: float
    via: str  # address(주소 API) | keyword(카카오 키워드 검색)


async def _retry_busy(fn: Callable[[], Awaitable], sleep=asyncio.sleep):
    for wait in (*BUSY_WAITS, None):
        try:
            return await fn()
        except Busy:
            if wait is None:
                raise
            await sleep(wait)


class JusoGeocoder:
    """정확한 주소 → 좌표. 두 API는 승인키가 따로다."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        search_key: str,
        coord_key: str,
        base_url: str = DEFAULT_BASE,
        sleep=asyncio.sleep,
    ):
        self.http, self.skey, self.ckey = http, search_key, coord_key
        self.base, self.sleep = base_url.rstrip("/"), sleep

    async def _call(self, path: str, params: dict) -> list[dict]:
        r = await self.http.get(
            f"{self.base}/{path}", params={**params, "resultType": "json"}, timeout=10
        )
        res = r.json().get("results", {})
        code = str(res.get("common", {}).get("errorCode"))
        if code == "E0007":
            raise Busy(code)
        if code not in ("0", "None"):
            raise JusoError(f"{path} 오류 {code}")
        return res.get("juso") or []

    async def search(self, keyword: str, gu: str | None) -> dict | None:
        """부산 주소 첫 건. 원본에 구가 있으면 같은 구만."""
        kw = clean_keyword(keyword)
        if not kw:
            return None
        params = {"confmKey": self.skey, "currentPage": 1, "countPerPage": 5, "keyword": kw}
        found = await _retry_busy(lambda: self._call("addrLinkApi.do", params), self.sleep)
        ok = [
            j
            for j in found
            if (j.get("siNm") or "").startswith("부산") and (gu is None or j.get("sggNm") == gu)
        ]
        return ok[0] if ok else None

    async def coord(self, j: dict) -> tuple[float, float] | None:
        params = {
            "confmKey": self.ckey,
            **{k: j[k] for k in ("admCd", "rnMgtSn", "udrtYn", "buldMnnm", "buldSlno")},
        }
        found = await _retry_busy(lambda: self._call("addrCoordApi.do", params), self.sleep)
        if not found or not found[0].get("entX"):
            return None
        return utmk_to_wgs84(float(found[0]["entX"]), float(found[0]["entY"]))

    async def geocode(self, address: str) -> GeoResult | None:
        if not precise(address):
            return None
        kw, gu = clean_keyword(address), district_of(address)
        j = await self.search(kw, gu)
        if j is None and (short := trim_tail(kw)):
            j = await self.search(short, gu)
        xy = await self.coord(j) if j else None
        if xy is None or not in_busan(*xy):
            return None
        return GeoResult(xy[0], xy[1], "address")


class KakaoKeywordGeocoder:
    """주소가 없거나 동까지만 있는 촬영지 → 카카오 키워드 검색 좌표(#25)."""

    def __init__(self, http: httpx.AsyncClient, rest_key: str, url: str = KAKAO_URL):
        self.http, self.key, self.url = http, rest_key, url

    async def _search(self, query: str) -> list[dict]:
        r = await self.http.get(
            self.url,
            params={"query": query, "size": 15},
            headers={"Authorization": f"KakaoAK {self.key}"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("documents") or []

    async def geocode(self, name: str, address: str | None) -> GeoResult | None:
        h = hints(name, address)
        base = clean_keyword(name)
        if not norm(base):
            return None
        query = " ".join(x for x in (h.gu or "부산", h.dong, base) if x)
        pick = choose(await self._search(query), base, h)
        return GeoResult(pick[0], pick[1], "keyword") if pick else None


def _place_ok(d: dict, h: Hints) -> bool:
    words = (d.get("address_name") or "").split()
    if not words or words[0] != "부산":
        return False
    if h.gu and h.gu not in words:
        return False
    if h.dong and not any(dong_key(w) == h.dong for w in words):
        return False
    return not h.area or any(w.startswith(h.area) for w in words)


def choose(docs: list[dict], name: str, h: Hints) -> tuple[float, float] | None:
    """검색 결과 중 채택할 좌표(위도, 경도). 판단에 쓴 응답은 버린다.

    이름이 같은 곳만 받는다. 다만 구·동이 맞으면 원본 이름이 결과 이름에 설명을 붙인 경우
    ('산엔청 고기집' ↔ '산엔청')도 받는다. 결과 이름이 더 긴 경우('황령산보쌈')는 받지 않는다.
    """
    target = norm(name)
    near = [d for d in docs if _place_ok(d, h)]
    same = [d for d in near if norm(d.get("place_name")) == target]
    if not same and (h.gu or h.dong):
        same = [d for d in near if len(n := norm(d.get("place_name"))) >= 2 and n in target][:1]
    pts = [(float(d["y"]), float(d["x"])) for d in same]
    pts = [p for p in pts if in_busan(*p)]
    if not pts:
        return None
    if any(distance_m(*pts[0], *p) > SAME_PLACE_M for p in pts[1:]):
        return None  # 동명 장소가 여러 곳: 어느 것인지 모름
    return round(pts[0][0], 6), round(pts[0][1], 6)
