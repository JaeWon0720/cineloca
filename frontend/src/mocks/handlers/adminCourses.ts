// API-024 목업: 추천 코스 후보 생성·수정·승인 (FR-614, UI-12). 관리자 로그인(adminGuard)이 필요하다.
// 후보 고르기·순서는 화면 확인용으로 단순하게 흉내 낸다(실제: 작품이 가장 많은 곳 기준 가까운 곳 + RoutePlanner).
// 응답 모양과 오류 코드는 backend app/api/routers/admin_courses.py와 같게 둔다.
import {
  COURSE_STOPS_MAX,
  COURSE_STOPS_MIN,
  type AdminCourseDetail,
  type AdminCoursePatch,
  type AdminCourseStatus,
  type AdminCourseSummary,
  type CourseCandidateRequest,
} from '../../api/admin.ts'
import type { Theme } from '../../api/courses.ts'
import type { LocationDetail, MetaResponse } from '../../api/locations.ts'
import { haversineM } from '../../lib/geo.ts'
import raw from '../data/f2.json'
import { fail, ok, type Handler } from '../http.ts'
import { adminGuard } from './admin.ts'
import { buildPlan } from './courses.ts'

const DATA = raw as unknown as { meta: MetaResponse; locations: LocationDetail[] }
const STAY_MIN = 20 // BR-13
const TITLE: Record<Theme, (name: string) => string> = {
  work: (n) => `「${n}」 촬영지 코스`,
  person: (n) => `${n} 출연작 촬영지 코스`,
  district: (n) => `${n} 촬영지 코스`,
}

type MockCourse = {
  id: number
  title: string
  theme: Theme
  theme_ref: string
  status: AdminCourseStatus
  approved_at: string | null
  start_station_id: string | null
  stops: { location_id: number; stay_min: number }[]
}

const INIT: MockCourse[] = [
  { id: 1, title: '원도심 영화 골목 걷기', theme: 'district', theme_ref: '중구', status: 'approved', approved_at: '2026-11-24T10:00:00+09:00', start_station_id: null, stops: [{ location_id: 11, stay_min: 20 }, { location_id: 7, stay_min: 30 }] },
  { id: 2, title: '「1987」 촬영지 코스', theme: 'work', theme_ref: '6', status: 'draft', approved_at: null, start_station_id: null, stops: [{ location_id: 22, stay_min: 20 }, { location_id: 20, stay_min: 20 }, { location_id: 19, stay_min: 20 }] },
]
let courses: MockCourse[] = INIT.map((c) => structuredClone(c))
let nextId = 3

/** 테스트에서 처음 상태로 돌린다 */
export function resetMockAdminCourses() {
  courses = INIT.map((c) => structuredClone(c))
  nextId = 3
}

const loc = (id: number) => DATA.locations.find((l) => l.id === id)

function detail(c: MockCourse): AdminCourseDetail {
  const points = c.stops.map((s) => loc(s.location_id)!)
  const st = DATA.meta.stations.find((s) => s.id === c.start_station_id)
  const start = st
    ? { type: 'station' as const, label: `${st.name}역(${st.line})`, lat: st.lat, lon: st.lon }
    : { type: 'location' as const, label: points[0].name, lat: points[0].lat, lon: points[0].lon }
  const plan = buildPlan(start, points, true)
  const stops = plan.stops.map((s, i) => ({ ...s, stay_min: c.stops[i].stay_min }))
  const stay = stops.reduce((n, s) => n + s.stay_min, 0)
  return {
    ...plan,
    stops,
    stay_min: stay,
    total_min: plan.travel_min + stay,
    optimized: false,
    id: c.id,
    title: c.title,
    theme: c.theme,
    theme_ref: c.theme_ref,
    status: c.status,
    approved_at: c.approved_at,
    start_station_id: c.start_station_id,
  }
}

function summary(c: MockCourse): AdminCourseSummary {
  const d = detail(c)
  return { id: c.id, title: c.title, theme: c.theme, theme_ref: c.theme_ref, status: c.status, approved_at: c.approved_at, start_station_id: c.start_station_id, stop_count: c.stops.length, total_min: d.total_min, total_distance_m: d.total_distance_m, problem: null }
}

function themePlaces(theme: Theme, ref: string): { name: string; places: LocationDetail[] } | null {
  const located = DATA.locations.filter((l) => l.located && l.lat != null)
  if (theme === 'district') return { name: ref, places: located.filter((l) => l.gu === ref) }
  if (theme === 'person') return null // 목업에는 인물 데이터가 없다
  const work = DATA.locations.flatMap((l) => l.works).find((w) => String(w.id) === ref)
  if (!work) return null
  return { name: work.title, places: located.filter((l) => l.works.some((w) => w.id === work.id)) }
}

function create(body: Partial<CourseCandidateRequest> | null) {
  const theme = body?.theme
  const ref = body?.theme_ref?.trim() ?? ''
  const max = body?.max_stops ?? 5
  if (!theme || !['work', 'person', 'district'].includes(theme) || !ref) return fail(422, 'INVALID_REQUEST', '요청 형식이 올바르지 않습니다: theme')
  if (max < COURSE_STOPS_MIN || max > COURSE_STOPS_MAX) return fail(422, 'INVALID_REQUEST', '요청 형식이 올바르지 않습니다: max_stops')
  if (theme === 'district' && !DATA.meta.districts.some((d) => d.name === ref)) return fail(400, 'INVALID_REQUEST', '부산의 구·군 이름을 골라 주세요.')
  if (theme !== 'district' && !/^\d+$/.test(ref)) return fail(400, 'INVALID_REQUEST', '작품·인물 번호는 숫자예요.')
  const found = themePlaces(theme, ref)
  if (!found) return fail(404, 'THEME_NOT_FOUND', '작품·인물을 찾을 수 없습니다.')
  const [seed, ...rest] = found.places
  if (!seed || rest.length === 0) return fail(409, 'NOT_ENOUGH_PLACES', '좌표가 있는 촬영지가 2곳 미만이라 코스를 만들 수 없어요.')
  rest.sort((a, z) => haversineM(seed.lat!, seed.lon!, a.lat!, a.lon!) - haversineM(seed.lat!, seed.lon!, z.lat!, z.lon!))
  const c: MockCourse = { id: nextId++, title: TITLE[theme](found.name), theme, theme_ref: ref, status: 'draft', approved_at: null, start_station_id: null, stops: [seed, ...rest.slice(0, max - 1)].map((l) => ({ location_id: l.id, stay_min: STAY_MIN })) }
  courses.push(c)
  return { status: 201, data: detail(c) }
}

function patch(id: number, body: AdminCoursePatch | null) {
  const c = courses.find((x) => x.id === id)
  if (!c) return fail(404, 'COURSE_NOT_FOUND', '추천 코스를 찾을 수 없습니다.')
  const b = body ?? {}
  const next = structuredClone(c)
  if (b.title !== undefined) {
    const t = b.title.trim()
    if (t.length < 1 || t.length > 60) return fail(400, 'INVALID_REQUEST', '제목은 1~60자예요.')
    next.title = t
  }
  if (b.stops !== undefined) {
    const ids = b.stops.map((s) => s.location_id)
    if (new Set(ids).size !== ids.length) return fail(400, 'INVALID_REQUEST', '같은 촬영지를 두 번 넣을 수 없습니다.')
    if (ids.length < COURSE_STOPS_MIN || ids.length > COURSE_STOPS_MAX) return fail(400, 'INVALID_REQUEST', `촬영지는 ${COURSE_STOPS_MIN}~${COURSE_STOPS_MAX}곳이어야 합니다.`)
    for (const i of ids) {
      const l = loc(i)
      if (!l) return fail(404, 'LOCATION_NOT_FOUND', `촬영지를 찾을 수 없습니다: [${i}]`)
      if (!l.located) return fail(409, 'LOCATION_NOT_LOCATED', '위치가 확인되지 않은 촬영지는 코스에 넣을 수 없어요.')
    }
    next.stops = b.stops.map((s) => ({ location_id: s.location_id, stay_min: s.stay_min ?? STAY_MIN }))
  }
  if (b.start_station_id !== undefined) {
    if (b.start_station_id && !DATA.meta.stations.some((s) => s.id === b.start_station_id)) return fail(404, 'STATION_NOT_FOUND', '역을 찾을 수 없습니다.')
    next.start_station_id = b.start_station_id
  }
  if (b.title !== undefined || b.stops !== undefined || b.start_station_id !== undefined) Object.assign(next, { status: 'draft', approved_at: null })
  if (b.status) Object.assign(next, { status: b.status, approved_at: b.status === 'approved' ? new Date().toISOString() : null })
  Object.assign(c, next)
  return ok(detail(c))
}

function remove(id: number) {
  const c = courses.find((x) => x.id === id)
  if (!c) return fail(404, 'COURSE_NOT_FOUND', '추천 코스를 찾을 수 없습니다.')
  if (c.status !== 'draft') return fail(409, 'COURSE_APPROVED', '승인된 코스는 지울 수 없어요. 먼저 승인을 취소해 주세요.')
  courses = courses.filter((x) => x.id !== id)
  return { status: 204, data: {} }
}

export const adminCourseHandlers: Handler[] = [
  {
    method: 'GET',
    path: /^\/admin\/courses$/,
    handle: ({ query }) => adminGuard() ?? ok({ items: courses.filter((c) => !query.get('status') || c.status === query.get('status')).filter((c) => !query.get('theme') || c.theme === query.get('theme')).map(summary) }),
  },
  { method: 'POST', path: /^\/admin\/courses$/, handle: ({ body }) => adminGuard() ?? create(body as CourseCandidateRequest) },
  {
    method: 'GET',
    path: /^\/admin\/courses\/(\d+)$/,
    handle: ({ params }) => {
      const c = courses.find((x) => x.id === Number(params[0]))
      return adminGuard() ?? (c ? ok(detail(c)) : fail(404, 'COURSE_NOT_FOUND', '추천 코스를 찾을 수 없습니다.'))
    },
  },
  { method: 'PATCH', path: /^\/admin\/courses\/(\d+)$/, handle: ({ params, body }) => adminGuard() ?? patch(Number(params[0]), body as AdminCoursePatch) },
  { method: 'DELETE', path: /^\/admin\/courses\/(\d+)$/, handle: ({ params }) => adminGuard() ?? remove(Number(params[0])) },
]
