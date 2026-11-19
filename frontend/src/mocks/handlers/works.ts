// F1 API 목업: API-001 작품 목록, API-002 작품 상세, API-003 관람등급, API-004 AI 작품 소개
import { NOTICE_VERSION } from '../../api/notice.ts'
import { WORKS_PAGE_SIZE, type WorkDetail, type WorkSummary } from '../../api/works.ts'
import raw from '../data/f1.json'
import { fail, ok, type Handler } from '../http.ts'

type IntroMock = Record<string, Record<'none' | 'partial', { sentences: string[]; dropped: number }>>
const DATA = raw as unknown as {
  works: WorkSummary[]
  workDetails: Record<string, WorkDetail>
  ratings: Record<string, string | null>
  intro: IntroMock
}
const norm = (s: string) => s.replace(/\s/g, '').toLowerCase()
const notFound = () => fail(404, 'WORK_NOT_FOUND', '작품을 찾을 수 없습니다')

function list(query: URLSearchParams) {
  const q = norm(query.get('q') ?? '')
  const genre = query.get('genre')
  const sort = query.get('sort') ?? 'title'
  const items = DATA.works
    .filter((w) => {
      const d = DATA.workDetails[String(w.id)]
      const hay = [w.title, w.director ?? '', ...(d?.cast ?? [])].map(norm)
      return (!q || hay.some((h) => h.includes(q))) && (!genre || w.genres.includes(genre))
    })
    .sort((a, z) =>
      sort === 'year'
        ? (z.release_year ?? 0) - (a.release_year ?? 0)
        : sort === 'locations'
          ? z.location_count - a.location_count
          : a.title.localeCompare(z.title, 'ko'),
    )
  const size = Math.min(Math.max(Number(query.get('size')) || WORKS_PAGE_SIZE, 1), 100)
  const page = Math.max(Number(query.get('page')) || 1, 1)
  const pageItems = items.slice((page - 1) * size, page * size)
  const seen = new Map(pageItems.flatMap((w) => DATA.workDetails[String(w.id)]?.sources ?? []).map((s) => [s.id, s]))
  return ok({ total: items.length, page, size, items: pageItems, sources: [...seen.values()] })
}

const DEGRADED = 'AI 소개를 만들지 못해 원문 줄거리와 촬영지를 보여 드려요'
const NOT_ELIGIBLE = 'KMDb와 연결된 작품만 AI 소개를 제공해요'

function intro(id: string, body: unknown, headers: Record<string, string>) {
  // 백엔드 require_notice와 같다: 이용 안내를 확인한 요청만(FR-701)
  if (headers['X-Notice-Version'] !== NOTICE_VERSION) return fail(403, 'NOTICE_REQUIRED', '이용 안내를 확인한 뒤 다시 시도해 주세요.')
  const w = DATA.workDetails[id]
  if (!w) return notFound()
  const level = (body as { spoiler?: string } | null)?.spoiler === 'partial' ? 'partial' : 'none'
  const base = { spoiler: level, reason: null, dropped_count: 0 }
  if (!w.kmdb_matched)
    return ok({ ...base, status: 'not_eligible', sentences: [], ai_generated: false, fallback: null, notice: NOT_ELIGIBLE, reason: 'not_matched' })
  const m = DATA.intro[id]?.[level]
  // 남은 문장이 2개 미만이면 원문 줄거리와 촬영지로 강등(FR-110)
  if (!m || m.sentences.length < 2)
    return ok({
      ...base,
      status: 'degraded',
      sentences: [],
      ai_generated: false,
      fallback: { synopsis: level === 'none' ? null : w.synopsis, locations: w.locations.map((l) => ({ name: l.name, gu: l.gu })) },
      notice: DEGRADED,
      reason: 'too_few_sentences',
    })
  return ok({ ...base, status: 'ok', sentences: m.sentences, ai_generated: true, fallback: null, notice: null, dropped_count: m.dropped })
}

export const workHandlers: Handler[] = [
  { method: 'GET', path: /^\/works$/, handle: ({ query }) => list(query) },
  {
    method: 'GET',
    path: /^\/works\/(\d+)$/,
    handle: ({ params }) => {
      const w = DATA.workDetails[params[0]]
      return w ? ok(w) : notFound()
    },
  },
  {
    method: 'GET',
    path: /^\/works\/(\d+)\/rating$/,
    handle: ({ params }) =>
      DATA.workDetails[params[0]]
        ? ok({ rating: DATA.ratings[params[0]] ?? null, sources: DATA.ratings[params[0]] ? [DATA.workDetails[params[0]].sources[0]] : [] })
        : notFound(),
  },
  { method: 'POST', path: /^\/works\/(\d+)\/intro$/, handle: ({ params, body, headers }) => intro(params[0], body, headers) },
]
