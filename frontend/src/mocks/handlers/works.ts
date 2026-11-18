// F1 API 목업: API-001 작품 목록(검색·장르·정렬), API-002 작품 상세
import { WORKS_PAGE_SIZE, type WorkDetail, type WorkSummary } from '../../api/works.ts'
import raw from '../data/f1.json'
import { fail, ok, type Handler } from '../http.ts'

const DATA = raw as unknown as { works: WorkSummary[]; workDetails: Record<string, WorkDetail> }
const norm = (s: string) => s.replace(/\s/g, '').toLowerCase()

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

export const workHandlers: Handler[] = [
  { method: 'GET', path: /^\/works$/, handle: ({ query }) => list(query) },
  {
    method: 'GET',
    path: /^\/works\/(\d+)$/,
    handle: ({ params }) => {
      const w = DATA.workDetails[params[0]]
      return w ? ok(w) : fail(404, 'WORK_NOT_FOUND', '작품을 찾을 수 없습니다')
    },
  },
]
