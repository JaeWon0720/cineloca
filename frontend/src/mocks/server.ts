// 목업 API 서버: 백엔드 없이 프론트만 띄워도 화면이 동작하게 하는 가짜 응답이다.
// 응답 모양은 백엔드 스키마(app/api/schemas)와 같게 유지한다. 기능별 처리기는 handlers/에 둔다.
import type { ApiRequest, ApiResponse } from '../api/client.ts'
import { commonHandlers } from './handlers/common.ts'
import { courseHandlers } from './handlers/courses.ts'
import { locationHandlers } from './handlers/locations.ts'
import { queryHandlers } from './handlers/query.ts'
import { stampHandlers } from './handlers/stamps.ts'
import { workHandlers } from './handlers/works.ts'
import { fail, type Handler } from './http.ts'

const HANDLERS: Handler[] = [...commonHandlers, ...locationHandlers, ...workHandlers, ...queryHandlers, ...courseHandlers, ...stampHandlers]
const DELAY_MS = import.meta.env.MODE === 'test' ? 0 : 150

export async function mockRequest(req: ApiRequest): Promise<ApiResponse> {
  if (DELAY_MS) await new Promise((r) => setTimeout(r, DELAY_MS))
  const [pathname, query = ''] = req.path.split('?')
  for (const h of HANDLERS) {
    if (h.method !== req.method) continue
    const m = pathname.match(h.path)
    // 실제 네트워크처럼 응답을 복사해 넘긴다(화면이 목업 데이터를 바꾸지 못하게)
    if (m) return structuredClone(h.handle({ params: m.slice(1), query: new URLSearchParams(query), body: req.body, headers: req.headers }))
  }
  return fail(404, 'NOT_FOUND', '요청한 주소를 찾을 수 없습니다')
}
