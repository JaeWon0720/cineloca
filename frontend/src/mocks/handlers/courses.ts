// F4 API 목업: API-009 추천 코스 목록, API-010 상세, API-011 나만의 코스 경로, API-012 주변 시설 추천.
// 경로 계산은 화면 확인용으로 단순하게 흉내 낸다(최근접 이웃만, 걷기 4km/h, 2.5km 넘으면 도시철도).
// 실제 순서 최적화(최근접 이웃 + 2-opt·Or-opt, FR-404)와 이동 시간(BR-12)은 백엔드가 계산한다.
import type {
  AmenityRequest,
  CourseLeg,
  CoursePlan,
  CourseStop,
  CourseSummary,
  PlanRequest,
  StartOut,
} from '../../api/courses.ts'
import { NOTICE_VERSION } from '../../api/notice.ts'
import type { LocationDetail, MetaResponse } from '../../api/locations.ts'
import { haversineM } from '../../lib/geo.ts'
import raw from '../data/f2.json'
import { fail, ok, type Handler } from '../http.ts'

const DATA = raw as unknown as { meta: MetaResponse; locations: LocationDetail[] }
const STAY_MIN = 30 // BR-13 체류 시간(목업 고정값)
const WALK_M_PER_MIN = 67
const invalid = (field: string) => fail(422, 'INVALID_REQUEST', `요청 형식이 올바르지 않습니다: ${field}`)

/** 추천 코스(목업). 실제로는 관리자가 승인한 코스만 나온다(FR-401, 생성·승인 FR-614 담당 미정). */
const RECOMMENDED: { id: number; title: string; theme: CourseSummary['theme']; theme_ref: string | null; ids: number[] }[] = [
  { id: 1, title: '원도심 영화 골목 걷기', theme: 'district', theme_ref: '중구', ids: [4, 6, 2] },
  { id: 2, title: '「1987」 촬영지 따라가기', theme: 'work', theme_ref: '6', ids: [22, 5, 20] },
]

function stopOf(l: LocationDetail, order: number): CourseStop {
  return { order, id: l.id, name: l.name, gu: l.gu, lat: l.lat!, lon: l.lon!, stay_min: STAY_MIN, cert: l.cert }
}

function nearestStation(lat: number, lon: number) {
  return DATA.meta.stations
    .map((s) => ({ s, d: haversineM(lat, lon, s.lat, s.lon) }))
    .sort((a, z) => a.d - z.d)[0]
}

function leg(from: { lat: number; lon: number }, to: { lat: number; lon: number }, fromOrder: number, toOrder: number): CourseLeg {
  const d = Math.round(haversineM(from.lat, from.lon, to.lat, to.lon))
  const walk = Math.max(1, Math.round(d / WALK_M_PER_MIN))
  const a = nearestStation(from.lat, from.lon)
  const b = nearestStation(to.lat, to.lon)
  if (d > 2500 && a && b && a.s.id !== b.s.id) {
    const toBoard = Math.round(a.d / WALK_M_PER_MIN)
    const fromAlight = Math.round(b.d / WALK_M_PER_MIN)
    const ride = Math.max(2, Math.round(haversineM(a.s.lat, a.s.lon, b.s.lat, b.s.lon) / 600)) + (a.s.line !== b.s.line ? 5 : 0)
    const sub = toBoard + ride + fromAlight
    if (sub < walk)
      return {
        from_order: fromOrder,
        to_order: toOrder,
        mode: 'subway',
        minutes: sub,
        distance_m: d,
        board: { id: a.s.id, name: a.s.name, line: a.s.line },
        alight: { id: b.s.id, name: b.s.name, line: b.s.line },
        walk_to_board_min: toBoard,
        ride_min: ride,
        walk_from_alight_min: fromAlight,
      }
  }
  return { from_order: fromOrder, to_order: toOrder, mode: 'walk', minutes: walk, distance_m: d }
}

export function buildPlan(start: StartOut, points: LocationDetail[], keepOrder: boolean): CoursePlan {
  let order = points
  const origin = start.lat != null && start.lon != null ? { lat: start.lat, lon: start.lon } : null
  if (!keepOrder) {
    // 최근접 이웃(목업): 출발지에서 가장 가까운 곳부터
    const left = [...points]
    order = []
    let cur = origin ?? { lat: left[0].lat!, lon: left[0].lon! }
    while (left.length) {
      left.sort((a, z) => haversineM(cur.lat, cur.lon, a.lat!, a.lon!) - haversineM(cur.lat, cur.lon, z.lat!, z.lon!))
      const next = left.shift()!
      order.push(next)
      cur = { lat: next.lat!, lon: next.lon! }
    }
  }
  const stops = order.map((l, i) => stopOf(l, i + 1))
  const legs: CourseLeg[] = []
  if (origin && stops.length) {
    const first = leg(origin, stops[0], 0, 1)
    if (first.distance_m > 0) legs.push(first)
  }
  for (let i = 1; i < stops.length; i++) legs.push(leg(stops[i - 1], stops[i], i, i + 1))
  const travel = legs.reduce((s, l) => s + l.minutes, 0)
  const stay = stops.reduce((s, x) => s + x.stay_min, 0)
  return {
    start,
    stops,
    legs,
    travel_min: travel,
    stay_min: stay,
    total_min: travel + stay,
    total_distance_m: legs.reduce((s, l) => s + l.distance_m, 0),
    optimized: !keepOrder,
    subway_data: true,
  }
}

function plan(body: Partial<PlanRequest>) {
  const ids = body.location_ids
  if (!Array.isArray(ids) || ids.length < 1 || ids.length > 10) return invalid('location_ids')
  const s = body.start
  if (!s) return invalid('start')
  const locs: LocationDetail[] = []
  for (const id of ids) {
    const l = DATA.locations.find((x) => x.id === id)
    if (!l) return fail(404, 'LOCATION_NOT_FOUND', '촬영지를 찾을 수 없습니다')
    if (!l.located || l.lat == null) return fail(409, 'LOCATION_NOT_LOCATED', `위치가 확인되지 않은 촬영지는 코스에 넣을 수 없어요: ${l.name}`)
    locs.push(l)
  }
  let start: StartOut
  let points = locs
  if (s.type === 'current') {
    if (s.lat == null || s.lon == null) return invalid('start.lat')
    start = { type: 'current', label: '현재 위치', lat: s.lat, lon: s.lon } // 실제 서버는 좌표를 돌려주지 않는다(BR-10)
  } else if (s.type === 'station') {
    const st = DATA.meta.stations.find((x) => x.id === s.id)
    if (!st) return fail(404, 'STATION_NOT_FOUND', '역을 찾을 수 없습니다')
    start = { type: 'station', label: `${st.name}역(${st.line})`, lat: st.lat, lon: st.lon }
  } else {
    // 출발지가 촬영지면 그 촬영지가 첫 방문 장소(정정표 11/18)
    const l = DATA.locations.find((x) => String(x.id) === s.id)
    if (!l || !l.located) return fail(404, 'LOCATION_NOT_FOUND', '촬영지를 찾을 수 없습니다')
    start = { type: 'location', label: l.name, lat: l.lat, lon: l.lon }
    points = [l, ...locs.filter((x) => x.id !== l.id)]
  }
  if (points.length < 2) return invalid('location_ids')
  if (s.type === 'location' && !body.fixed_order) {
    const [first, ...rest] = points
    const p = buildPlan({ ...start }, rest, false)
    const all = buildPlan(start, [first, ...p.stops.map((x) => DATA.locations.find((l) => l.id === x.id)!)], true)
    return ok({ ...all, optimized: true, start: { ...start, lat: s.type === 'location' ? start.lat : null } })
  }
  const out = buildPlan(start, points, !!body.fixed_order)
  return ok({ ...out, start: { ...out.start, lat: s.type === 'current' ? null : out.start.lat, lon: s.type === 'current' ? null : out.start.lon } })
}

function detail(id: number) {
  const c = RECOMMENDED.find((x) => x.id === id)
  if (!c) return fail(404, 'COURSE_NOT_FOUND', '추천 코스를 찾을 수 없습니다')
  const locs = c.ids.map((i) => DATA.locations.find((l) => l.id === i)!).filter((l) => l?.located)
  const first = locs[0]
  const p = buildPlan({ type: 'location', label: first.name, lat: first.lat, lon: first.lon }, locs, true)
  return ok({ ...p, id: c.id, title: c.title, theme: c.theme, theme_ref: c.theme_ref })
}

function summaries(theme: string | null) {
  return RECOMMENDED.filter((c) => !theme || c.theme === theme).map((c) => {
    const d = (detail(c.id).data as CoursePlan) ?? null
    return {
      id: c.id,
      title: c.title,
      theme: c.theme,
      theme_ref: c.theme_ref,
      stop_count: d.stops.length,
      total_min: d.total_min,
      total_distance_m: d.total_distance_m,
    }
  })
}

// ---------- 주변 시설(목업): 가게 이름은 화면 확인용 예시다
const FOOD_NAMES = ['(예시) 골목 국밥집', '(예시) 바다 앞 카페', '(예시) 시장 분식', '(예시) 언덕 위 찻집']
const LODGING_NAMES = ['(예시) 항구 호텔', '(예시) 게스트하우스 바다', '(예시) 레지던스 원도심']

function amenities(body: Partial<AmenityRequest>, headers: Record<string, string>) {
  if (headers['X-Notice-Version'] !== NOTICE_VERSION) return fail(403, 'NOTICE_REQUIRED', '이용 안내를 확인한 뒤 다시 시도해 주세요.')
  const ids = body.location_ids ?? []
  if (ids.length < 2 || ids.length > 10) return invalid('location_ids')
  if ((body.text ?? '').length > 200) return invalid('text')
  const locs = ids.map((i) => DATA.locations.find((l) => l.id === i))
  if (locs.some((l) => !l)) return fail(404, 'LOCATION_NOT_FOUND', '촬영지를 찾을 수 없습니다')
  const text = body.text?.trim() ?? ''
  const masked = /01[016789][-\s]?\d{3,4}[-\s]?\d{4}/.test(text)
  const sel = body.selected
  const wantsLodging = text.includes('숙소') || text.includes('호텔') || !!sel?.kinds?.includes('lodging')
  const wantsFood = !sel?.kinds?.length || sel.kinds.includes('food') || /밥|카페|식당|맛집/.test(text)
  const kinds = [...(wantsFood ? ['food'] : []), ...(wantsLodging ? ['lodging'] : [])] as ('food' | 'lodging')[]
  const food = sel?.food?.length ? sel.food : text.includes('카페') ? ['카페'] : ['밥']
  const segments = []
  for (let i = 1; i < locs.length && kinds.includes('food'); i++) {
    const a = locs[i - 1]!
    const b = locs[i]!
    const mid = { lat: ((a.lat ?? 0) + (b.lat ?? 0)) / 2, lon: ((a.lon ?? 0) + (b.lon ?? 0)) / 2 }
    // 세 번째 구간은 '근처 시설 없음'(0건) 안내를 보여 주려고 비운다
    const empty = i === 3
    segments.push({
      key: `food-${i}`,
      kind: 'food',
      from_order: i,
      to_order: i + 1,
      anchor: `${a.name}~${b.name} 사이`,
      notice: empty ? '이 구간 근처에는 조건에 맞는 시설이 없어요' : null,
      picks: empty
        ? []
        : [0, 1].map((k) => ({
            content_id: `M${i}${k}`,
            name: FOOD_NAMES[(i + k) % FOOD_NAMES.length],
            category: food[k % food.length],
            address: `부산 ${a.gu ?? ''}`,
            lat: mid.lat + k * 0.001,
            lon: mid.lon + k * 0.001,
            distance_m: 180 + 120 * k + 40 * i,
            matched: [`분류: ${food[k % food.length]}`],
            reason: k === 0 ? `${a.name}에서 ${b.name}로 가는 길에 들르기 좋은 ${food[0]} 집이에요.` : `${food[k % food.length]} 조건에 맞고 구간 중간에서 가까워요.`,
            ai_generated: k === 0 && !sel,
            directions_url: 'https://map.kakao.com',
          })),
    })
  }
  if (kinds.includes('lodging')) {
    const last = locs[locs.length - 1]!
    segments.push({
      key: 'lodging',
      kind: 'lodging',
      from_order: locs.length,
      to_order: null,
      anchor: `마지막 촬영지 ${last.name} 근처`,
      notice: null,
      picks: LODGING_NAMES.map((n, k) => ({
        content_id: `L${k}`,
        name: n,
        category: ['호텔', '게스트하우스·호스텔', '콘도·레지던스'][k],
        address: `부산 ${last.gu ?? ''}`,
        lat: (last.lat ?? 0) + 0.002 * k,
        lon: (last.lon ?? 0) + 0.002,
        distance_m: 300 + 250 * k,
        matched: ['분류: 숙박'],
        reason: `마지막 촬영지에서 걸어갈 수 있는 숙소예요.`,
        ai_generated: false,
        directions_url: 'https://map.kakao.com',
      })),
    })
  }
  return ok({
    conditions: {
      status: sel ? 'selected' : text ? 'ai' : 'manual',
      kinds,
      food: kinds.includes('food') ? food : [],
      lodging: kinds.includes('lodging') ? (sel?.lodging ?? []) : [],
      keywords: [],
      masked,
    },
    notices: masked ? ['개인정보로 보이는 내용을 가리고 조건만 읽었어요.'] : [],
    segments,
    sources: [{ id: 'TOURAPI', provider: '한국관광공사', name: 'TourAPI 위치기반 관광정보', base_date: null, url: 'https://api.visitkorea.or.kr' }],
  })
}

export const courseHandlers: Handler[] = [
  { method: 'GET', path: /^\/courses$/, handle: ({ query }) => ok({ items: summaries(query.get('theme')) }) },
  { method: 'GET', path: /^\/courses\/(\d+)$/, handle: ({ params }) => detail(Number(params[0])) },
  { method: 'POST', path: /^\/courses\/plan$/, handle: ({ body }) => plan((body ?? {}) as Partial<PlanRequest>) },
  { method: 'POST', path: /^\/courses\/amenities$/, handle: ({ body, headers }) => amenities((body ?? {}) as Partial<AmenityRequest>, headers) },
]
