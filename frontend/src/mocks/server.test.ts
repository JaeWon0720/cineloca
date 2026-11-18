import { describe, expect, it } from 'vitest'
import type { SourcesInfo } from '../api/types.ts'
import { mockRequest } from './server.ts'

async function call<T>(method: string, path: string, body?: unknown) {
  const r = await mockRequest({ method, path, body, headers: { 'X-Session-Id': 'test' } })
  return r as { status: number; data: T }
}

describe('목업 API: 공통', () => {
  it('출처 대장은 백엔드 출처 카드와 같은 필드를 쓴다', async () => {
    const { status, data } = await call<SourcesInfo>('GET', '/sources')
    expect(status).toBe(200)
    expect(data.ledger.length).toBeGreaterThan(0)
    expect(Object.keys(data.ledger[0])).toEqual(expect.arrayContaining(['id', 'provider', 'name', 'base_date', 'url', 'license', 'policy']))
  })

  it('없는 주소는 SDD 오류 형식으로 404를 준다', async () => {
    const r = await call<{ error: { code: string; message: string } }>('GET', '/nope')
    expect(r.status).toBe(404)
    expect(r.data.error.code).toBe('NOT_FOUND')
  })
})
