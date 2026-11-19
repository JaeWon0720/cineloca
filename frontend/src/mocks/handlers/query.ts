// F3 API 목업: API-013 대화형 탐색(질의·고르기·칩 지우기), API-014 필터 패널 검색, API-015 새 대화.
// 실제 서버는 AI가 조건을 뽑고 사전 대조·검색 템플릿으로 찾는다. 목업은 낱말 맞추기로 흉내만 낸다.
// 문구는 백엔드 query_service.py와 같게 둔다.
import { NOTICE_VERSION } from '../../api/notice.ts'
import type { LocationDetail, MetaResponse } from '../../api/locations.ts'
import { EMPTY_FILTERS, QUERY_MAX, type ChipKey, type Filters, type Hit } from '../../api/query.ts'
import raw from '../data/f2.json'
import { fail, ok, type Handler } from '../http.ts'

const DATA = raw as unknown as { meta: MetaResponse; locations: LocationDetail[]; locationGenres: Record<string, string[]> }

const SCOPE_NOTICE = "부산의 영화·드라마 촬영지와 작품에 대해서만 답할 수 있어요. 예: '해운대에서 찍은 영화'"
const AI_DOWN_NOTICE = 'AI가 지금 답하지 못해 필터로 찾을 수 있게 열어 두었어요'
const NOT_FOUND_NOTICE = '조건에 맞는 촬영지를 찾지 못했어요. 필터로 조건을 바꿔 보세요'
const MASK_NOTICE = '개인정보로 보이는 내용을 가렸습니다'
const RELAX_ORDER: ChipKey[] = ['travel', 'years', 'genres', 'districts']
const RELAX_LABEL: Partial<Record<ChipKey, string>> = { travel: '이동 조건', years: '개봉연도', genres: '장르', districts: '구·군' }

type Session = { last: Filters | null; pending: { name: string; options: { id: string; label: string }[]; filters: Filters } | null }
const sessions = new Map<string, Session>()
let seq = 0
function session(id: string | undefined): [string, Session] {
  if (id && sessions.has(id)) return [id, sessions.get(id)!]
  const nid = `mock-s${++seq}`
  const s: Session = { last: null, pending: null }
  sessions.set(nid, s)
  return [nid, s]
}

const ALL_WORKS = new Map<string, { id: string; name: string }>()
for (const l of DATA.locations) for (const w of l.works) ALL_WORKS.set(w.title, { id: String(w.id), name: w.title })

function chips(f: Filters) {
  const out: { key: ChipKey; label: string }[] = []
  out.push(...f.works.map((r) => ({ key: 'works' as const, label: `작품: ${r.name}` })))
  out.push(...f.persons.map((r) => ({ key: 'persons' as const, label: `인물: ${r.name}` })))
  if (f.genres.length) out.push({ key: 'genres', label: `장르: ${f.genres.join(', ')}` })
  if (f.districts.length) out.push({ key: 'districts', label: `구·군: ${f.districts.join(', ')}` })
  if (f.year_from || f.year_to) out.push({ key: 'years', label: `개봉: ${f.year_from ?? ''}~${f.year_to ?? ''}` })
  if (f.anchor) out.push({ key: 'anchor', label: `기준: ${f.anchor.ref?.name ?? (f.anchor.type === 'current' ? '내 위치' : '아무 역')}` })
  if (f.travel) out.push({ key: 'travel', label: `${f.travel.mode === 'walk' ? '도보' : '도시철도'} ${f.travel.max_min}분 이내` })
  return out
}

function match(f: Filters): Hit[] {
  return DATA.locations
    .filter((l) => l.located && l.lat != null && l.lon != null)
    .filter((l) => !f.districts.length || f.districts.includes(l.gu ?? ''))
    .filter((l) => !f.works.length || l.works.some((w) => f.works.some((r) => r.name === w.title)))
    .filter((l) => !f.genres.length || DATA.locationGenres[String(l.id)]?.some((g) => f.genres.includes(g)))
    .filter((l) => {
      const ys = l.works.map((w) => w.release_year ?? 0)
      return (!f.year_from || Math.max(...ys) >= f.year_from) && (!f.year_to || Math.min(...ys) <= f.year_to)
    })
    .map((l) => ({
      location_id: l.id,
      name: l.name,
      gu: l.gu,
      lat: l.lat!,
      lon: l.lon!,
      works: l.works.map((w) => ({ title: w.title, year: w.release_year })),
      station: l.nearest_station?.name ?? null,
      walk_min: l.nearest_station ? Math.round(l.nearest_station.walk_min) : null,
      source_ids: l.sources.map((s) => s.id),
    }))
}

function search(f: Filters) {
  let cur = { ...f }
  let hits = match(cur)
  const relaxed: string[] = []
  // 0건이면 정해진 순서로 조건을 하나씩 푼다(FR-305)
  for (const k of RELAX_ORDER) {
    if (hits.length) break
    const has = k === 'years' ? cur.year_from || cur.year_to : k === 'travel' ? cur.travel : (cur[k as 'genres' | 'districts'] as unknown[]).length
    if (!has) continue
    cur = k === 'years' ? { ...cur, year_from: null, year_to: null } : k === 'travel' ? { ...cur, travel: null } : { ...cur, [k]: [] }
    relaxed.push(RELAX_LABEL[k]!)
    hits = match(cur)
  }
  return { hits, relaxed, used: cur }
}

function answer(sid: string, s: Session, f: Filters, notices: string[], masked: boolean, unknown: string[] = []) {
  s.last = f
  const { hits, relaxed } = search(f)
  const found = hits.length > 0
  const top = hits.slice(0, 20)
  const summary = found
    ? [
        `조건에 맞는 촬영지 ${hits.length}곳을 찾았어요.`,
        `첫 번째로 보여 드리는 곳은 ${top[0].gu ?? '부산'}의 ${top[0].name}이에요.`,
        ...(top[0].station ? [`${top[0].station}역에서 걸어서 갈 수 있어요.`] : []),
      ].slice(0, 3)
    : []
  return ok({
    status: found ? 'ok' : 'not_found',
    session_id: sid,
    notices: found ? notices : [...notices, NOT_FOUND_NOTICE],
    chips: chips(f),
    relaxed,
    total: hits.length,
    hits: top,
    filter_panel: found ? null : f,
    masked,
    unknown,
    summary,
    ai_generated: found,
    choices: [],
  })
}

function parse(text: string): { f: Filters; unknown: string[] } {
  const f: Filters = { ...EMPTY_FILTERS, works: [], districts: [], genres: [] }
  for (const d of DATA.meta.districts) {
    const stem = d.name.replace(/[구군]$/, '') // '해운대'처럼 줄여 말해도 알아듣되, 한 글자('서')는 '에서'와 헷갈려 뺀다
    if (text.includes(d.name) || (stem.length >= 2 && text.includes(stem))) f.districts.push(d.name)
  }
  for (const [title, ref] of ALL_WORKS) if (text.includes(title)) f.works.push(ref)
  for (const g of DATA.meta.genres) if (g.split('·').some((p) => text.includes(p))) f.genres.push(g)
  const y = text.match(/(19|20)\d{2}/)
  if (y && /이후|부터/.test(text)) f.year_from = Number(y[0])
  else if (y && /이전|까지/.test(text)) f.year_to = Number(y[0])
  if (/영화들|작품 목록|무슨 작품/.test(text)) f.result_type = 'work'
  const unknown = /「([^」]+)」/.exec(text)?.[1]
  return { f, unknown: unknown && !ALL_WORKS.has(unknown) ? [unknown] : [] }
}

function query(body: { text?: string } | null, headers: Record<string, string>) {
  if (headers['X-Notice-Version'] !== NOTICE_VERSION) return fail(403, 'NOTICE_REQUIRED', '이용 안내를 확인한 뒤 다시 시도해 주세요.')
  const text = (body?.text ?? '').trim()
  if (!text) return fail(400, 'INVALID_QUERY', '질문을 입력해 주세요')
  if (text.length > QUERY_MAX) return fail(400, 'INVALID_QUERY', `질문은 ${QUERY_MAX}자까지 쓸 수 있어요`)
  const [sid, s] = session(headers['X-Session-Id'])
  const masked = /01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}|@/.test(text)
  const notices = masked ? [MASK_NOTICE] : []
  const base = { session_id: sid, chips: [], relaxed: [], total: 0, hits: [], filter_panel: null, masked, unknown: [], summary: [], ai_generated: false, choices: [] }
  if (/날씨|주식|비트코인|전화번호|연락처|사는 곳/.test(text)) return ok({ ...base, status: 'out_of_scope', notices: [...notices, SCOPE_NOTICE] })
  const { f, unknown } = parse(text)
  if (/AI ?장애/.test(text)) return ok({ ...base, status: 'ai_unavailable', notices: [...notices, AI_DOWN_NOTICE], filter_panel: f })
  // 후속 질의: '그중'으로 시작하면 직전 조건에 더한다(FR-308)
  const merged = /^그중|^거기서/.test(text) && s.last
    ? { ...s.last, districts: f.districts.length ? f.districts : s.last.districts, genres: f.genres.length ? f.genres : s.last.genres, works: [...s.last.works, ...f.works] }
    : f
  if (text.includes('친구')) {
    // 같은 이름 작품이 둘이면 고르게 한다(FR-303)
    const options = [
      { id: '9001', label: '친구 (2001)' },
      { id: '9002', label: '친구 2 (2013)' },
    ]
    s.pending = { name: '친구', options, filters: merged }
    return ok({ ...base, status: 'choose', notices, chips: chips(merged), choices: [{ key: 'works', name: '친구', options }] })
  }
  return answer(sid, s, merged, notices, masked, unknown)
}

function choose(body: { option_id?: string } | null, headers: Record<string, string>) {
  const [sid, s] = session(headers['X-Session-Id'])
  if (!s.pending) return fail(400, 'INVALID_QUERY', '고를 항목이 없어요')
  const opt = s.pending.options.find((o) => o.id === body?.option_id)
  if (!opt) return fail(400, 'INVALID_QUERY', '고를 항목이 없어요')
  const f = { ...s.pending.filters, works: [...s.pending.filters.works, { id: opt.id, name: opt.label.replace(/ \(\d+\)$/, '') }] }
  s.pending = null
  return answer(sid, s, f, [], false)
}

function removeChip(body: { key?: ChipKey } | null, headers: Record<string, string>) {
  const [sid, s] = session(headers['X-Session-Id'])
  if (!s.last || !body?.key) return fail(400, 'INVALID_QUERY', '고를 항목이 없어요')
  const k = body.key
  const f: Filters =
    k === 'years' ? { ...s.last, year_from: null, year_to: null } : k === 'anchor' || k === 'travel' ? { ...s.last, [k]: null } : { ...s.last, [k]: [] }
  return answer(sid, s, f, [], false)
}

function filterSearch(body: { conditions?: Filters } | null) {
  const f = { ...EMPTY_FILTERS, ...(body?.conditions ?? {}) }
  const { hits, relaxed } = search(f)
  return ok({
    status: hits.length ? 'ok' : 'not_found',
    notices: hits.length ? [] : [NOT_FOUND_NOTICE],
    chips: chips(f),
    relaxed,
    total: hits.length,
    hits: hits.slice(0, 20),
    filter_panel: null,
  })
}

export const queryHandlers: Handler[] = [
  { method: 'POST', path: /^\/query$/, handle: ({ body, headers }) => query(body as { text?: string }, headers) },
  { method: 'POST', path: /^\/query\/choose$/, handle: ({ body, headers }) => choose(body as { option_id?: string }, headers) },
  { method: 'POST', path: /^\/query\/chips\/remove$/, handle: ({ body, headers }) => removeChip(body as { key?: ChipKey }, headers) },
  {
    method: 'DELETE',
    path: /^\/query\/session$/,
    handle: ({ headers }) => {
      sessions.delete(headers['X-Session-Id'] ?? '')
      return { status: 204, data: {} }
    },
  },
  { method: 'POST', path: /^\/search$/, handle: ({ body }) => filterSearch(body as { conditions?: Filters }) },
]
