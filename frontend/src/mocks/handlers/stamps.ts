// F5 API 목업: API-016 방문 인증, API-017 토큰 확인, API-018 완주 배지.
// 토큰은 'mock.'으로 시작하는 가짜 문자열이다(서명 없음). 실제 서버는 JWT(HS256)로 서명한다.
import type { BadgeRequest, StampRequest } from '../../api/stamps.ts'
import type { LocationDetail } from '../../api/locations.ts'
import { haversineM } from '../../lib/geo.ts'
import raw from '../data/f2.json'
import { fail, ok, type Handler } from '../http.ts'

const DATA = raw as unknown as { locations: LocationDetail[] }
const MAX_ACCURACY_M = 50
const COURSE_STOPS: Record<number, { title: string; ids: number[] }> = {
  1: { title: '원도심 영화 골목 걷기', ids: [4, 6, 2] },
  2: { title: '「1987」 촬영지 따라가기', ids: [22, 5, 20] },
}
const EXCLUDED: Record<string, string> = {
  no_coordinates: '위치가 확인되지 않은 촬영지라 방문 인증을 할 수 없어요.',
  private: '사유지라 방문 인증을 하지 않아요. 외부에서만 관람해 주세요.',
  residential: '주민이 사는 곳이라 방문 인증을 하지 않아요. 외부에서만 관람해 주세요.',
  school: '학교라 방문 인증을 하지 않아요. 외부에서만 관람해 주세요.',
  excluded: '방문 인증 대상에서 제외된 촬영지예요.',
}

type Claims = { t: 'stamp'; loc: number; at: string } | { t: 'badge'; course: string; title: string | null; at: string }
const enc = (c: Claims) => `mock.${btoa(encodeURIComponent(JSON.stringify(c)))}`
function dec(token: string): Claims | null {
  if (!token.startsWith('mock.')) return null
  try {
    return JSON.parse(decodeURIComponent(atob(token.slice(5)))) as Claims
  } catch {
    return null
  }
}
const stampedIds = (tokens: string[]) =>
  new Set(tokens.map(dec).flatMap((c) => (c?.t === 'stamp' ? [c.loc] : [])))

function stamp(b: Partial<StampRequest>) {
  if (typeof b.location_id !== 'number' || typeof b.lat !== 'number' || typeof b.lon !== 'number' || typeof b.accuracy_m !== 'number')
    return fail(422, 'INVALID_REQUEST', '요청 형식이 올바르지 않습니다: location_id')
  const l = DATA.locations.find((x) => x.id === b.location_id)
  if (!l) return fail(404, 'LOCATION_NOT_FOUND', '촬영지를 찾을 수 없습니다')
  if (!l.cert.eligible) return fail(409, 'CERT_EXCLUDED', EXCLUDED[l.cert.excluded_reason ?? 'excluded'])
  if (stampedIds(b.held ?? []).has(l.id)) return ok({ ok: false, token: null, reason: 'already_certified', distance_m: null })
  // 정확도 50m 초과면 거리를 알려 주지 않는다(정정표 11/18)
  if (b.accuracy_m > MAX_ACCURACY_M) return ok({ ok: false, token: null, reason: 'low_accuracy', distance_m: null })
  const d = Math.round(haversineM(b.lat, b.lon, l.lat!, l.lon!))
  if (d > l.cert.radius_m) return ok({ ok: false, token: null, reason: 'out_of_range', distance_m: d })
  return ok({ ok: true, token: enc({ t: 'stamp', loc: l.id, at: new Date().toISOString() }), reason: null, distance_m: null })
}

function verify(tokens: string[]) {
  const valid = []
  const badges = []
  let invalid = 0
  for (const t of tokens) {
    const c = dec(t)
    if (!c) {
      invalid++
      continue
    }
    if (c.t === 'badge') {
      badges.push({ course_key: c.course, course_title: c.title, issued_at: c.at })
      continue
    }
    const l = DATA.locations.find((x) => x.id === c.loc)
    if (!l) {
      invalid++
      continue
    }
    const works = [...l.works].sort((a, z) => (z.release_year ?? 0) - (a.release_year ?? 0))
    valid.push({
      location: { id: l.id, name: l.name },
      works: works.slice(0, 3).map(({ id, title, release_year }) => ({ id, title, release_year })),
      work_count: l.works.length,
      certified_at: c.at,
    })
  }
  return ok({ valid, badges, invalid_count: invalid, unavailable_count: 0 })
}

function badge(b: Partial<BadgeRequest>) {
  const ref = b.course ?? {}
  let ids: number[]
  let key: string
  let title: string | null = null
  if (ref.id != null) {
    const c = COURSE_STOPS[ref.id]
    if (!c) return fail(404, 'COURSE_NOT_FOUND', '추천 코스를 찾을 수 없습니다')
    ids = c.ids
    key = `course:${ref.id}`
    title = c.title
  } else if (ref.location_ids?.length) {
    ids = ref.location_ids
    key = `saved:${[...ids].sort((a, z) => a - z).join('-')}`
  } else return fail(422, 'INVALID_REQUEST', '요청 형식이 올바르지 않습니다: course')
  const targets = ids.filter((i) => DATA.locations.find((l) => l.id === i)?.cert.eligible)
  // 완주 배지는 인증 대상 2곳 이상인 코스만(BR-09, 정정표 11/18)
  if (targets.length < 2) return ok({ ok: false, token: null, reason: 'too_few_targets', course_key: key, missing: [] })
  const have = stampedIds(b.stamps ?? [])
  const missing = targets.filter((i) => !have.has(i))
  if (missing.length) return ok({ ok: false, token: null, reason: 'missing_stamps', course_key: key, missing })
  return ok({ ok: true, token: enc({ t: 'badge', course: key, title, at: new Date().toISOString() }), reason: null, course_key: key, missing: [] })
}

export const stampHandlers: Handler[] = [
  { method: 'POST', path: /^\/stamps$/, handle: ({ body }) => stamp((body ?? {}) as Partial<StampRequest>) },
  { method: 'POST', path: /^\/stamps\/verify$/, handle: ({ body }) => verify(((body as { tokens?: string[] }) ?? {}).tokens ?? []) },
  { method: 'POST', path: /^\/badges$/, handle: ({ body }) => badge((body ?? {}) as Partial<BadgeRequest>) },
]
