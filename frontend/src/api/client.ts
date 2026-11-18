// ApiClient (COMP-01): 모든 요청에 X-Session-Id(탭마다 무작위)를 붙이고,
// AI 요청에는 사용자가 확인한 이용 안내 버전(X-Notice-Version, FR-701)을 붙인다.
//
// 백엔드 API가 아직 없어서 기본값은 목업 모드다. 프론트만 띄워도 모든 화면이 동작한다.
// 백엔드가 준비되면 .env에 VITE_API_MOCK=false 를 넣는다(그러면 /api 로 실제 요청).
import { store } from '../lib/storage.ts'

export { NOTICE_VERSION } from './notice.ts'
export const API_MOCK = import.meta.env.VITE_API_MOCK !== 'false'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly retryAfter: number | null

  constructor(status: number, code: string, message: string, retryAfter: number | null = null) {
    super(message)
    this.status = status
    this.code = code
    this.retryAfter = retryAfter
  }
}

/** 오류 응답의 retry_after(초)가 있으면 언제 다시 하면 되는지 문장에 붙인다(429 등). */
export function withRetryHint(message: string, retryAfter: number | null | undefined): string {
  if (!retryAfter || retryAfter <= 0) return message
  const wait = retryAfter >= 60 ? `${Math.ceil(retryAfter / 60)}분` : `${retryAfter}초`
  return `${message} ${wait} 뒤에 다시 시도해 주세요.`
}

function sessionId(): string {
  try {
    let id = sessionStorage.getItem('cineloca:sid')
    if (!id) {
      id = crypto.randomUUID()
      sessionStorage.setItem('cineloca:sid', id)
    }
    return id
  } catch {
    return 'anonymous'
  }
}

export type ApiRequest = { method: string; path: string; body?: unknown; headers: Record<string, string> }
export type ApiResponse = { status: number; data: unknown }

async function send(req: ApiRequest): Promise<ApiResponse> {
  if (API_MOCK) {
    const { mockRequest } = await import('../mocks/server.ts')
    return mockRequest(req)
  }
  const res = await fetch(`/api${req.path}`, {
    method: req.method,
    headers: req.body === undefined ? req.headers : { ...req.headers, 'Content-Type': 'application/json' },
    body: req.body === undefined ? undefined : JSON.stringify(req.body),
  })
  return { status: res.status, data: await res.json().catch(() => ({})) }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'X-Session-Id': sessionId() }
  const notice = store.get<string>('notice')
  if (notice) headers['X-Notice-Version'] = notice
  let res: ApiResponse
  try {
    res = await send({ method, path, body, headers })
  } catch {
    throw new ApiError(0, 'NETWORK', '서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.')
  }
  if (res.status >= 400) {
    const e = (res.data as { error?: { code?: string; message?: string; retry_after?: number | null } } | null)?.error ?? {}
    const fallback = res.status === 429 ? '요청이 너무 많습니다.' : '요청을 처리하지 못했습니다.'
    const retryAfter = e.retry_after ?? null
    throw new ApiError(res.status, e.code ?? 'ERROR', withRetryHint(e.message ?? fallback, retryAfter), retryAfter)
  }
  return res.data as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body: unknown) => request<T>('POST', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
}
