// API-020 목업: 관리자 로그인·상태·로그아웃 (FR-615).
// 실제 서버는 환경변수 ADMIN_PASSWORD_HASH로 확인하고 HttpOnly 쿠키를 준다. 목업은 메모리에 로그인 여부만 둔다.
// 목업 비밀번호는 화면 개발용이며 실제 서버에는 쓰이지 않는다. 문구는 backend app/api/routers/admin.py와 같게 둔다.
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

export const adminHandlers: Handler[] = [
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
