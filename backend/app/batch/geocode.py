"""주소 → 좌표 (FR-605, TBD-04). PoC p3_geocode.py를 옮기고 실패 원인 두 가지를 보완했다.

1) 주소가 있으면 도로명주소 검색 API(admCd·rnMgtSn·건물번호)
   → 좌표제공 API(entX·entY, UTM-K EPSG:5179) → WGS84
2) 검색 결과가 없으면 건물번호(또는 지번) 뒤의 상호·층·설명을 잘라 한 번 더 검색한다(보완 1)
3) 좌표를 믿기 어려운 경우는 찾지 않는다(#25): 주소가 없는 촬영지(장소명 검색은 엉뚱한 건물로
   잡히는 일이 많음)와 번지·건물번호 없이 동까지만 있는 주소(그 동의 아무 건물이 잡힘).
   이런 곳은 '좌표 없음'(FR-210)으로 두고, 다른 방법으로 찾을지는 #25에서 정한다.
4) E0007(짧은 시간 과다 요청)은 두 API 모두 잠깐 쉬고 다시 부른다(보완 2. PoC는 검색만 재시도)
부산 범위 밖 좌표는 버린다. 두 API는 승인키가 따로다.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from pyproj import Transformer

DEFAULT_BASE = "https://business.juso.go.kr/addrlink"
BUSAN_BBOX = (34.85, 35.45, 128.70, 129.40)  # lat_min, lat_max, lon_min, lon_max
BUSY_WAITS = (2, 5, 10, 20)
_UTMK = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
_PAREN = re.compile(r"\(.*?\)|\[.*?\]")
_FORBIDDEN = re.compile(r"[%=<>\[\]'\";]")
# '… 센텀5로 55 1층 ○○' → '… 센텀5로 55', '… 기룡리 630 ○○' → '… 기룡리 630'
_TAIL = re.compile(r"^(.*?(?:로|길)\s*\d+(?:-\d+)?|.*?(?:동|리|가)\s*(?:산\s*)?\d+(?:-\d+)?)\s.+$")


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
    """괄호 안을 뺀 주소에 번지·건물번호(숫자)가 있으면 건물 하나로 좁혀진다."""
    return bool(address) and any(ch.isdigit() for ch in _PAREN.sub(" ", address))


def utmk_to_wgs84(x: float, y: float) -> tuple[float, float]:
    lon, lat = _UTMK.transform(x, y)
    return round(lat, 6), round(lon, 6)


def in_busan(lat: float, lon: float) -> bool:
    return BUSAN_BBOX[0] <= lat <= BUSAN_BBOX[1] and BUSAN_BBOX[2] <= lon <= BUSAN_BBOX[3]


@dataclass(frozen=True)
class GeoResult:
    lat: float
    lon: float
    road_addr: str | None
    via: str  # address


async def _retry_busy(fn: Callable[[], Awaitable], sleep=asyncio.sleep):
    for wait in (*BUSY_WAITS, None):
        try:
            return await fn()
        except Busy:
            if wait is None:
                raise
            await sleep(wait)


class JusoGeocoder:
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

    async def search(self, keyword: str) -> dict | None:
        """부산 주소 첫 건."""
        kw = clean_keyword(keyword)
        if not kw:
            return None
        params = {"confmKey": self.skey, "currentPage": 1, "countPerPage": 5, "keyword": kw}
        found = await _retry_busy(lambda: self._call("addrLinkApi.do", params), self.sleep)
        busan = [j for j in found if (j.get("siNm") or "").startswith("부산")]
        return busan[0] if busan else None

    async def coord(self, j: dict) -> tuple[float, float] | None:
        params = {
            "confmKey": self.ckey,
            **{k: j[k] for k in ("admCd", "rnMgtSn", "udrtYn", "buldMnnm", "buldSlno")},
        }
        found = await _retry_busy(lambda: self._call("addrCoordApi.do", params), self.sleep)
        if not found or not found[0].get("entX"):
            return None
        return utmk_to_wgs84(float(found[0]["entX"]), float(found[0]["entY"]))

    async def geocode(self, address: str | None) -> GeoResult | None:
        if not precise(address):
            return None
        kw = clean_keyword(address)
        j = await self.search(kw)
        if j is None and (short := trim_tail(kw)):
            j = await self.search(short)
        if j is None:
            return None
        xy = await self.coord(j)
        if xy is None or not in_busan(*xy):
            return None
        return GeoResult(xy[0], xy[1], j.get("roadAddr"), "address")
