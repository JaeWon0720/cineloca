// F2 API 목업: API-005 근접·필터 검색, API-006 촬영지 상세, API-007 선택 목록
// 백엔드 예시 응답(app/api/examples.py)과 같은 규칙으로 걸러 낸다.
import type { LocationDetail, LocationHit, LocationSearchRequest, MetaResponse } from '../../api/locations.ts'
import { haversineM } from '../../lib/geo.ts'
import raw from '../data/f2.json'
import { fail, ok, type Handler } from '../http.ts'

const DATA = raw as unknown as { meta: MetaResponse; locations: LocationDetail[]; locationGenres: Record<string, string[]> }
// 백엔드(app/api/errors.py)와 같은 코드·문구
const invalid = (field: string) => fail(422, 'INVALID_REQUEST', `요청 형식이 올바르지 않습니다: ${field}`)

function search(req: Partial<LocationSearchRequest>) {
  const base = req.base
  if (!base || typeof base.lat !== 'number' || typeof base.lon !== 'number') return invalid('base')
  const radius = req.radius_m ?? DATA.meta.default_radius_m
  if (!DATA.meta.radius_options.includes(radius)) return invalid('radius_m')
  if (req.year_from && req.year_to && req.year_from > req.year_to) return invalid('year_from')

  const items: LocationHit[] = []
  for (const l of DATA.locations) {
    if (!l.located || l.lat == null || l.lon == null) continue // 좌표 없는 촬영지는 지도에 나오지 않는다 (FR-210)
    const d = Math.round(haversineM(base.lat, base.lon, l.lat, l.lon))
    const years = l.works.map((w) => w.release_year ?? 0)
    if (d > radius) continue
    if (req.gu?.length && !req.gu.includes(l.gu ?? '')) continue
    if (req.genres?.length && !DATA.locationGenres[String(l.id)]?.some((g) => req.genres!.includes(g))) continue
    if (req.year_from && Math.max(...years) < req.year_from) continue
    if (req.year_to && Math.min(...years) > req.year_to) continue
    items.push({
      id: l.id,
      name: l.name,
      gu: l.gu,
      lat: l.lat,
      lon: l.lon,
      distance_m: d,
      works: l.works.slice(0, 3).map(({ id, title, release_year }) => ({ id, title, release_year })),
      work_count: l.works.length,
      nearest_station: l.nearest_station,
      directions_url: l.directions_url ?? '',
    })
  }
  items.sort((a, z) => a.distance_m - z.distance_m)
  const seen = new Map(DATA.locations.flatMap((l) => (items.some((i) => i.id === l.id) ? l.sources : [])).map((s) => [s.id, s]))
  return ok({ radius_m: radius, total: items.length, items, sources: [...seen.values()] })
}

export const locationHandlers: Handler[] = [
  { method: 'GET', path: /^\/meta$/, handle: () => ok(DATA.meta) },
  { method: 'POST', path: /^\/locations\/search$/, handle: ({ body }) => search((body ?? {}) as Partial<LocationSearchRequest>) },
  {
    method: 'GET',
    path: /^\/locations\/(\d+)$/,
    handle: ({ params }) => {
      const l = DATA.locations.find((x) => x.id === Number(params[0]))
      return l ? ok(l) : fail(404, 'LOCATION_NOT_FOUND', '촬영지를 찾을 수 없습니다')
    },
  },
]
