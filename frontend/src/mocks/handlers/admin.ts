// API-020 목업: 관리자 로그인·상태·로그아웃 (FR-615).
// 실제 서버는 환경변수 ADMIN_PASSWORD_HASH로 확인하고 HttpOnly 쿠키를 준다. 목업은 메모리에 로그인 여부만 둔다.
// 목업 비밀번호는 화면 개발용이며 실제 서버에는 쓰이지 않는다. 문구는 backend app/api/routers/admin.py와 같게 둔다.
import {
  CERT_RADIUS_MAX,
  CERT_RADIUS_MIN,
  PLACE_TAGS,
  SAFETY_NOTE_MAX,
  type AdminLocationAttrs,
  type AdminLocationPatch,
  type ReviewAction,
  type ReviewItem,
  type SyncCounts,
  type SyncOverview,
  type SyncReport,
} from '../../api/admin.ts'
import type { LocationDetail } from '../../api/locations.ts'
import raw from '../data/f2.json'
import type { ApiResponse } from '../../api/client.ts'
import { MOCK_ADMIN_LOGIN } from '../adminLogin.ts'
import { ok, type Handler } from '../http.ts'

const MAX_FAILURES = 5
const LOCK_S = 10 * 60
const SESSION_TTL_S = 2 * 60 * 60

const state = { failures: 0, lockedUntil: 0, expiresAt: 0 }

/** 테스트에서 처음 상태로 돌린다 */
export function resetMockAdmin() {
  Object.assign(state, { failures: 0, lockedUntil: 0, expiresAt: 0 })
  sync.active = null
  sync.nextId = 13
  reviews = REVIEWS_INIT.map((r) => ({ ...r }))
  attrs.clear()
}

/** 테스트에서 세션 만료를 흉내 낸다 */
export function expireMockAdmin() {
  state.expiresAt = 0
}

const now = () => Date.now() / 1000
const err = (status: number, code: string, message: string, retry_after: number | null = null): ApiResponse => ({
  status,
  data: { error: { code, message, retry_after } },
})
const session = () => ok({ authenticated: true, expires_at: new Date(state.expiresAt * 1000).toISOString() })
const required = () => err(401, 'ADMIN_REQUIRED', '관리자 로그인이 필요합니다.')

/** 다른 관리자 목업(API-021~024)이 함께 쓰는 확인: 로그인 안 됐으면 401을 돌려준다 */
export function adminGuard(): ApiResponse | null {
  return state.expiresAt > now() ? null : required()
}

function login(body: { password?: string } | null): ApiResponse {
  const t = now()
  if (state.lockedUntil > t) {
    return err(429, 'ADMIN_LOCKED', '비밀번호를 5회 틀려 10분 동안 로그인할 수 없습니다.', Math.ceil(state.lockedUntil - t))
  }
  if (body?.password === MOCK_ADMIN_LOGIN) {
    state.failures = 0
    state.expiresAt = t + SESSION_TTL_S
    return session()
  }
  state.failures += 1
  if (state.failures >= MAX_FAILURES) {
    state.failures = 0
    state.lockedUntil = t + LOCK_S
    return err(429, 'ADMIN_LOCKED', '비밀번호를 5회 틀려 10분 동안 로그인할 수 없습니다.', LOCK_S)
  }
  return err(401, 'ADMIN_AUTH_FAILED', `비밀번호가 맞지 않습니다. 남은 시도 ${MAX_FAILURES - state.failures}회`)
}

// ── API-021 동기화 요청·리포트 목업: 최근 3회(성공·가드 중단·성공)와 오늘 호출 수
const counts = (works: number, locations: number, matched: number, review: number, located: number): SyncCounts => ({
  fetched: { 'DATA-01': locations, 'DATA-02': 312, 'DATA-04': works },
  works,
  locations,
  match: { matched, review, unmatched: works - matched - review },
  geo: { located, failed: locations - located, by_source: { address: located - 40, keyword: 40, none: locations - located } },
  unknown_genres: [],
})
const rate = (a: number, b: number) => (b ? Math.round((a / b) * 10000) / 10000 : null)
const report = (run_id: number, trigger: SyncReport['trigger'], status: SyncReport['status'], day: string, c: SyncCounts | null, prev: SyncCounts | null, extra: Partial<SyncReport> = {}): SyncReport => ({
  run_id,
  trigger,
  status,
  started_at: `${day}T03:00:05+09:00`,
  finished_at: `${day}T03:12:41+09:00`,
  counts: c,
  rates: c ? { match_rate: rate(c.match.matched, c.works), review_rate: rate(c.match.review, c.works), geo_rate: rate(c.geo.located, c.locations) } : null,
  prev_counts: prev,
  guard: [],
  warnings: [],
  failures: [],
  ...extra,
})
const C10 = counts(431, 1352, 351, 12, 1301)
const C12 = counts(436, 1359, 355, 9, 1310)
const C12_GUARDED = counts(201, 640, 160, 4, 600)
const REPORTS: SyncReport[] = [
  report(12, 'manual', 'succeeded', '2026-11-18', C12, C10, {
    counts: { ...C12, unknown_genres: ['다큐드라마'] },
    warnings: ['장르 묶음표에 없는 KMDb 장르 1종(다큐드라마)은 장르 필터에서 빠집니다. 묶음표에 추가해 주세요'],
    failures: [{ dataset: 'DATA-02', step: 'fetch', reason: 'TimeoutError: 응답 시간 초과(30초). 다음 실행에서 다시 받습니다' }],
  }),
  report(11, 'schedule', 'guarded', '2026-11-15', C12_GUARDED, C10, {
    guard: ['촬영지 수가 직전보다 53% 줄었습니다(1,352 → 640). 반영하지 않았습니다'],
  }),
  report(10, 'schedule', 'succeeded', '2026-11-08', C10, null),
]
const sync: { active: SyncOverview['active']; nextId: number } = { active: null, nextId: 13 }

function syncOverview(): SyncOverview {
  return {
    active: sync.active,
    alerts: ['11/15 정기 동기화가 건수 가드로 중단됐습니다. 리포트 #11을 확인해 주세요'],
    reports: REPORTS,
    usage_day: '2026-11-23',
    usage: [
      { key: 'gemini', count: 182, limit: 250, ratio: 0.728, level: 'warning' },
      { key: 'DATA-07', count: 1204, limit: 300000, ratio: 0.004, level: 'ok' },
      { key: 'DATA-09', count: 31, limit: 1000, ratio: 0.031, level: 'ok' },
      { key: 'DATA-04', count: 12, limit: null, ratio: null, level: 'ok' },
    ],
    schedule: { interval_days: 7, last_started_at: '2026-11-15T03:00:05+09:00', next_due_at: '2026-11-22T03:00:05+09:00' },
  }
}

function requestSync(): ApiResponse {
  if (sync.active) return ok({ run_id: sync.active.run_id, created: false, message: '이미 대기·실행 중인 동기화가 있습니다.' })
  sync.active = { run_id: sync.nextId++, trigger: 'manual', status: 'requested', requested_at: new Date().toISOString(), started_at: null }
  return { status: 202, data: { run_id: sync.active.run_id, created: true, message: '동기화를 요청했습니다. 1분 안에 시작됩니다.' } }
}

// ── API-022 검수 큐 목업: 규칙 매칭 4건·AI 매칭 1건·태그 3건(AI 실패 1건 포함)
const at = '2026-11-18T03:10:00+09:00'
const item = (id: number, kind: ReviewItem['kind'], target_id: number, target_name: string, target_year: number | null, proposer: ReviewItem['proposer'], score: number | null, proposal: ReviewItem['proposal']): ReviewItem => ({
  id,
  kind,
  target_type: kind === 'tag' ? 'location' : 'work',
  target_id,
  target_name,
  target_year,
  proposer,
  score,
  status: 'pending',
  proposal,
  created_at: at,
  handled_at: null,
})
const REVIEWS_INIT: ReviewItem[] = [
  item(1, 'match', 201, '대외비', 2023, 'rule', 0.82, { kmdb_id: 'K24589', kmdb_title: '대외비', prod_year: '2020', rel_date: '20230301' }),
  item(2, 'match', 202, '신과함께', 2017, 'rule', 0.71, { kmdb_id: 'K31342', kmdb_title: '신과함께(금연편)', prod_year: '2018', rel_date: null }),
  item(3, 'match', 203, '해운대 연인들', 2012, 'rule', 0.64, { kmdb_id: 'K09876', kmdb_title: '해운대', prod_year: '2009', rel_date: '20090722' }),
  item(4, 'match', 202, '신과함께', 2017, 'ai', null, { verdict: 'not_listed', kmdb_id: null, title: '신과함께', year: 2017, reason: '후보가 단편(금연편)이라 본편과 다름' }),
  item(5, 'match', 204, '친구', 2001, 'rule', 0.9, { kmdb_id: 'K00321', kmdb_title: '친구', prod_year: '2001', rel_date: '20010331' }),
  item(6, 'tag', 5, '온천천', null, 'ai', null, { tags: ['공원·산'], reason: '하천 산책로라 공원으로 봄' }),
  item(7, 'tag', 6, '충무동 새벽시장', null, 'rule', null, { tags: ['시장'], reason: "이름에 '시장'" }),
  item(8, 'tag', 3, '구 동부산대학교', null, 'ai', null, { tags: [], reason: null, error: 'AI_UNAVAILABLE' }),
]
let reviews: ReviewItem[] = REVIEWS_INIT.map((r) => ({ ...r }))

function listReviews(q: URLSearchParams): ApiResponse {
  const status = q.get('status') ?? 'pending'
  const kind = q.get('kind')
  const page = Math.max(1, Number(q.get('page') ?? 1))
  const size = Math.min(50, Math.max(1, Number(q.get('size') ?? 20)))
  const rows = reviews
    .filter((r) => (status === 'pending' ? r.status === 'pending' : status === 'handled' ? r.status !== 'pending' : true))
    .filter((r) => !kind || r.kind === kind)
  const pending = reviews.filter((r) => r.status === 'pending').length
  return ok({ total: rows.length, page, size, counts: { pending, handled: reviews.length - pending }, items: rows.slice((page - 1) * size, page * size) })
}

function handleReview(id: number, body: ReviewAction | null): ApiResponse {
  const r = reviews.find((x) => x.id === id)
  if (!r) return err(404, 'REVIEW_NOT_FOUND', '검수 항목을 찾지 못했습니다.')
  if (r.status !== 'pending') return err(409, 'REVIEW_ALREADY_HANDLED', '이미 처리된 항목입니다.')
  if (!body || !['approve', 'modify', 'reject'].includes(body.action)) return err(400, 'INVALID_REQUEST', '처리 방법을 골라 주세요.')
  if (body.action === 'modify') {
    if (r.kind === 'tag') {
      if (!body.tags?.length || body.tags.some((t) => !(PLACE_TAGS as readonly string[]).includes(t))) return err(400, 'INVALID_REQUEST', '태그는 장소 유형 목록에서 골라 주세요.')
      r.proposal = { ...r.proposal, tags: body.tags }
    } else {
      if (!body.kmdb_id || !/^K\d{5}$/.test(body.kmdb_id)) return err(400, 'INVALID_REQUEST', 'KMDb 번호는 K와 숫자 5자리예요(예: K24589).')
      r.proposal = { ...r.proposal, kmdb_id: body.kmdb_id }
    }
  }
  if (body.action === 'approve' && r.kind === 'tag' && !(r.proposal as { tags: string[] }).tags.length) return err(400, 'INVALID_REQUEST', '제안된 태그가 없어 승인할 수 없습니다. 수정으로 태그를 골라 주세요.')
  r.status = body.action === 'approve' ? 'approved' : body.action === 'modify' ? 'modified' : 'rejected'
  r.handled_at = new Date().toISOString()
  return ok(r)
}

// ── API-023 촬영지 속성 목업: f2.json 촬영지를 바탕으로 고친 값만 메모리에 둔다
const LOCS = (raw as unknown as { locations: LocationDetail[] }).locations
const attrs = new Map<number, AdminLocationAttrs>()
function locAttrs(id: number): AdminLocationAttrs | null {
  if (attrs.has(id)) return attrs.get(id)!
  const l = LOCS.find((x) => x.id === id)
  if (!l) return null
  return { id, name: l.name, gu: l.gu, address: l.address, located: l.located, tags: [...l.tags], caution: l.caution, safety_note: l.safety_note, cert_radius_m: l.cert.radius_m, cert_excluded: l.cert.excluded_reason === 'excluded' }
}

function patchLocation(id: number, body: AdminLocationPatch | null): ApiResponse {
  const cur = locAttrs(id)
  if (!cur) return err(404, 'LOCATION_NOT_FOUND', '촬영지를 찾을 수 없습니다.')
  const b = body ?? {}
  if (b.cert_radius_m != null && (!Number.isInteger(b.cert_radius_m) || b.cert_radius_m < CERT_RADIUS_MIN || b.cert_radius_m > CERT_RADIUS_MAX))
    return err(400, 'INVALID_REQUEST', `인증 반경은 ${CERT_RADIUS_MIN}~${CERT_RADIUS_MAX}m 사이 정수예요.`)
  if (b.caution !== undefined && b.caution !== null && !['private', 'residential', 'school'].includes(b.caution)) return err(400, 'INVALID_REQUEST', '주의 구분 값이 올바르지 않습니다.')
  if (b.safety_note && b.safety_note.length > SAFETY_NOTE_MAX) return err(400, 'INVALID_REQUEST', `안전 안내는 ${SAFETY_NOTE_MAX}자까지예요.`)
  if (b.tags && b.tags.some((t) => !(PLACE_TAGS as readonly string[]).includes(t))) return err(400, 'INVALID_REQUEST', '태그는 장소 유형 목록에서 골라 주세요.')
  const next = { ...cur, ...b, safety_note: b.safety_note === undefined ? cur.safety_note : b.safety_note?.trim() || null }
  attrs.set(id, next)
  return ok(next)
}

export const adminHandlers: Handler[] = [
  { method: 'GET', path: /^\/admin\/reviews$/, handle: ({ query }) => adminGuard() ?? listReviews(query) },
  { method: 'PATCH', path: /^\/admin\/reviews\/(\d+)$/, handle: ({ params, body }) => adminGuard() ?? handleReview(Number(params[0]), body as ReviewAction) },
  {
    method: 'GET',
    path: /^\/admin\/locations\/(\d+)$/,
    handle: ({ params }) => adminGuard() ?? (locAttrs(Number(params[0])) ? ok(locAttrs(Number(params[0]))) : err(404, 'LOCATION_NOT_FOUND', '촬영지를 찾을 수 없습니다.')),
  },
  { method: 'PATCH', path: /^\/admin\/locations\/(\d+)$/, handle: ({ params, body }) => adminGuard() ?? patchLocation(Number(params[0]), body as AdminLocationPatch) },
  { method: 'GET', path: /^\/admin\/sync$/, handle: () => adminGuard() ?? ok(syncOverview()) },
  { method: 'POST', path: /^\/admin\/sync$/, handle: () => adminGuard() ?? requestSync() },
  { method: 'POST', path: /^\/admin\/session$/, handle: ({ body }) => login(body as { password?: string }) },
  { method: 'GET', path: /^\/admin\/session$/, handle: () => adminGuard() ?? session() },
  {
    method: 'DELETE',
    path: /^\/admin\/session$/,
    handle: () => {
      state.expiresAt = 0
      return { status: 204, data: {} }
    },
  },
]
