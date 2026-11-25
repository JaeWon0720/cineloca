// 계약 대조 1단계 (#115, 5.2): 화면이 기대하는 응답(목업)을 API별로 모은다.
// 목업은 화면 타입(src/api/*.ts)에 맞춰 만든 응답이라, 이것을 백엔드 응답 스키마에 대 보면
// '화면 타입 ↔ 실제 API 형식'이 어긋난 곳을 찾을 수 있다(2단계: backend/tests/test_contract_mocks.py).
// CONTRACT_OUT=경로 로 돌리면 모은 응답을 JSON 파일로 남긴다. 평소 npm test에서는 모으기만 확인한다.
import { describe, expect, it } from 'vitest'
import { NOTICE_VERSION } from '../api/notice.ts'
import { mockRequest } from '../mocks/server.ts'

type Case = { name: string; method: 'GET' | 'POST'; path: string; body?: unknown }
type Dumped = Case & { status: number; data: unknown }

const H = { 'X-Notice-Version': NOTICE_VERSION }
const BASE = { lat: 35.1015, lon: 129.0287 }

async function call(c: Case, headers: Record<string, string> = H): Promise<Dumped> {
  const r = await mockRequest({ method: c.method, path: c.path, body: c.body, headers })
  return { ...c, status: r.status, data: r.data }
}

async function collect(): Promise<Dumped[]> {
  const out: Dumped[] = []
  const add = async (c: Case, headers?: Record<string, string>) => {
    const d = await call(c, headers)
    out.push(d)
    return d
  }
  await add({ name: 'API-007 선택 목록', method: 'GET', path: '/meta' })
  const search = await add({ name: 'API-005 촬영지 검색', method: 'POST', path: '/locations/search', body: { base: BASE, radius_m: 3000 } })
  const locId = (search.data as { items: { id: number }[] }).items[0].id
  await add({ name: 'API-006 촬영지 상세', method: 'GET', path: `/locations/${locId}` })
  const works = await add({ name: 'API-001 작품 목록', method: 'GET', path: '/works?page=1&size=20' })
  const workId = (works.data as { items: { id: number }[] }).items[0].id
  await add({ name: 'API-002 작품 상세', method: 'GET', path: `/works/${workId}` })
  await add({ name: 'API-002 작품 상세(KMDb 연결)', method: 'GET', path: '/works/6' })
  await add({ name: 'API-003 관람등급', method: 'GET', path: '/works/6/rating' })
  await add({ name: 'API-004 AI 작품 소개', method: 'POST', path: '/works/6/intro', body: { spoiler: 'none' } })
  const courses = await add({ name: 'API-009 추천 코스 목록', method: 'GET', path: '/courses' })
  const courseId = (courses.data as { items: { id: number }[] }).items[0]?.id ?? 1
  const course = await add({ name: 'API-010 추천 코스 상세', method: 'GET', path: `/courses/${courseId}` })
  const stops = (course.data as { stops: { id: number }[] }).stops.map((s) => s.id)
  await add({ name: 'API-011 나만의 코스', method: 'POST', path: '/courses/plan', body: { start: { type: 'location', id: String(stops[0]) }, location_ids: stops, fixed_order: true } })
  await add({ name: 'API-012 주변 시설', method: 'POST', path: '/courses/amenities', body: { location_ids: stops, categories: ['food'] } })
  await add({ name: 'API-013 질의', method: 'POST', path: '/query', body: { text: '해운대에서 찍은 영화' } })
  await add({ name: 'API-013 질의(범위 밖)', method: 'POST', path: '/query', body: { text: '오늘 날씨 어때' } })
  await add({ name: 'API-014 필터 검색', method: 'POST', path: '/search', body: { conditions: { districts: ['해운대구'] } } })
  const loc = (await call({ name: '', method: 'GET', path: `/locations/${stops[0]}` })).data as { lat: number; lon: number }
  const st = await add({ name: 'API-016 방문 인증', method: 'POST', path: '/stamps', body: { location_id: stops[0], lat: loc.lat, lon: loc.lon, accuracy_m: 10 } })
  const token = (st.data as { token: string | null }).token
  await add({ name: 'API-017 토큰 확인', method: 'POST', path: '/stamps/verify', body: { tokens: token ? [token] : [] } })
  await add({ name: 'API-018 완주 배지', method: 'POST', path: '/badges', body: { course: { type: 'recommended', id: courseId }, stamps: token ? [token] : [] } })
  return out
}

describe('계약 대조용 목업 응답 모으기 (#115)', () => {
  it('화면이 쓰는 API 응답을 모두 모은다', async () => {
    const out = await collect()
    const bad = out.filter((d) => d.status >= 400).map((d) => `${d.name} ${d.status}`)
    expect(bad).toEqual([])
    expect(out.length).toBeGreaterThanOrEqual(18)
    // 프론트 tsconfig에는 Node 타입이 없어 process·fs를 느슨하게 꺼낸다(테스트는 Node에서 돈다)
    const file = (globalThis as unknown as { process?: { env: Record<string, string | undefined> } }).process?.env.CONTRACT_OUT
    if (file) {
      const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as { writeFileSync: (f: string, d: string) => void }
      fs.writeFileSync(file, JSON.stringify(out, null, 2))
    }
  })
})
