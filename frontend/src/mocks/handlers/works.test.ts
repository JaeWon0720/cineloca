import { describe, expect, it } from 'vitest'
import type { WorkDetail, WorkListResponse } from '../../api/works.ts'
import { mockRequest } from '../server.ts'

async function call<T>(path: string) {
  const r = await mockRequest({ method: 'GET', path, headers: { 'X-Session-Id': 'test' } })
  return r as { status: number; data: T }
}

describe('목업 API: F1 작품 (API-001~002)', () => {
  it('작품 목록을 이름순으로 준다', async () => {
    const { data } = await call<WorkListResponse>('/works')
    expect(data.total).toBe(data.items.length)
    const t = data.items.map((w) => w.title)
    expect(t).toEqual([...t].sort((a, z) => a.localeCompare(z, 'ko')))
  })

  it('배우 이름으로 찾고, 최신순으로 정렬한다', async () => {
    const { data } = await call<WorkListResponse>(`/works?q=${encodeURIComponent('하정우')}&sort=year`)
    expect(data.total).toBeGreaterThan(0)
    const y = data.items.map((w) => w.release_year ?? 0)
    expect(y).toEqual([...y].sort((a, z) => z - a))
  })

  it('한 쪽에 정한 수만큼 나눠 준다 (FR-101)', async () => {
    const first = await call<WorkListResponse>('/works?size=3&page=1')
    const third = await call<WorkListResponse>('/works?size=3&page=3')
    expect(first.data.items).toHaveLength(3)
    expect(first.data.total).toBe(7)
    expect(third.data.items).toHaveLength(1)
    expect(new Set([...first.data.items, ...third.data.items].map((w) => w.id)).size).toBe(4)
  })

  it('기본 한 쪽 크기는 20편이다', async () => {
    const { data } = await call<WorkListResponse>('/works')
    expect(data.size).toBe(20)
    expect(data.page).toBe(1)
  })

  it('상세에는 부산 촬영지와 출처가 있다', async () => {
    const { data } = await call<WorkDetail>('/works/1')
    expect(data.title).toBe('리바운드')
    expect(data.locations.length).toBe(data.location_count)
    expect(data.sources.length).toBeGreaterThan(0)
  })

  it('없는 작품은 WORK_NOT_FOUND', async () => {
    const r = await call<{ error: { code: string } }>('/works/999')
    expect(r.status).toBe(404)
    expect(r.data.error.code).toBe('WORK_NOT_FOUND')
  })
})
