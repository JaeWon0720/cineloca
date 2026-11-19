// GeoProbe: 현재 위치를 5초 안에 얻는다. 이번 요청에만 쓰고 저장하지 않는다 (NFR-003, BR-10).
export type Fix = { lat: number; lon: number; accuracy: number }

export function getPosition(timeoutMs = 5000): Promise<Fix> {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new Error('이 브라우저는 위치 기능을 지원하지 않습니다.'))
      return
    }
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy }),
      (e) =>
        reject(
          new Error(
            e.code === e.PERMISSION_DENIED
              ? '위치 권한이 거부되어 있습니다. 브라우저 설정에서 위치를 허용해 주세요.'
              : '현재 위치를 가져오지 못했습니다.',
          ),
        ),
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 0 },
    )
  })
}

export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = 6371000
  const rad = Math.PI / 180
  const dp = (lat2 - lat1) * rad
  const dl = (lon2 - lon1) * rad
  const a = Math.sin(dp / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dl / 2) ** 2
  return 2 * r * Math.asin(Math.sqrt(a))
}

export const fmtDist = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${m}m`)
export const stationLabel = (s: string) => (s.endsWith('역') ? s : `${s}역`)
/** 분을 '1시간 20분'처럼 읽기 쉽게 */
export const fmtMin = (m: number) => (m >= 60 ? `${Math.floor(m / 60)}시간${m % 60 ? ` ${m % 60}분` : ''}` : `${m}분`)
