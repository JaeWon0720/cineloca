import { describe, expect, it } from 'vitest'
import type { LocationDetail, LocationSearchResponse, MetaResponse } from '../../api/locations.ts'
import { mockRequest } from '../server.ts'

async function call<T>(method: string, path: string, body?: unknown) {
  const r = await mockRequest({ method, path, body, headers: { 'X-Session-Id': 'test' } })
  return r as { status: number; data: T }
}
const BUSAN_STATION = { lat: 35.115049, lon: 129.041193 }

describe('목업 API: F2 지도 (API-005~007)', () => {
  it('선택 목록에 기본 기준 위치·반경·16개 구·군이 있다', async () => {
    const { data } = await call<MetaResponse>('GET', '/meta')
    expect(data.default_base.label).toBe('부산역')
    expect(data.radius_options).toEqual([500, 1000, 3000])
    expect(data.districts).toHaveLength(16)
  })

  it('반경 안 촬영지를 거리순으로 주고, 좌표 없는 곳은 뺀다', async () => {
    const { data } = await call<LocationSearchResponse>('POST', '/locations/search', { base: BUSAN_STATION, radius_m: 3000 })
    expect(data.total).toBe(data.items.length)
    expect(data.total).toBeGreaterThan(0)
    const d = data.items.map((x) => x.distance_m)
    expect(d).toEqual([...d].sort((a, b) => a - b))
    expect(d.every((x) => x <= 3000)).toBe(true)
    expect(data.items.every((x) => x.works.length <= 3 && x.work_count >= x.works.length)).toBe(true)
    expect(data.sources.length).toBeGreaterThan(0)
  })

  it('구·군과 연도 필터를 모두 만족하는 곳만 준다', async () => {
    const { data } = await call<LocationSearchResponse>('POST', '/locations/search', {
      base: { lat: 35.090146, lon: 129.067539 },
      radius_m: 3000,
      gu: ['영도구'],
      year_from: 2023,
      year_to: 2023,
    })
    expect(data.total).toBeGreaterThan(0)
    expect(data.items.every((x) => x.gu === '영도구' && x.works.some((w) => w.release_year === 2023))).toBe(true)
  })

  it('장르는 KMDb 장르 묶음(12개) 이름으로 거른다', async () => {
    const { data: meta } = await call<MetaResponse>('GET', '/meta')
    expect(meta.genres).toHaveLength(12)
    expect(meta.genres).toContain('범죄·느와르')
    const all = await call<LocationSearchResponse>('POST', '/locations/search', { base: BUSAN_STATION, radius_m: 3000 })
    const crime = await call<LocationSearchResponse>('POST', '/locations/search', { base: BUSAN_STATION, radius_m: 3000, genres: ['범죄·느와르'] })
    expect(crime.data.total).toBeGreaterThan(0)
    expect(crime.data.total).toBeLessThan(all.data.total)
  })

  it('기준 위치가 없거나 반경이 목록 밖이면 422 INVALID_REQUEST', async () => {
    const noBase = await call<{ error: { code: string; message: string } }>('POST', '/locations/search', { radius_m: 1000 })
    expect(noBase.status).toBe(422)
    expect(noBase.data.error).toMatchObject({ code: 'INVALID_REQUEST', message: '요청 형식이 올바르지 않습니다: base' })
    expect((await call('POST', '/locations/search', { base: BUSAN_STATION, radius_m: 30000 })).status).toBe(422)
  })

  it('상세: 좌표가 없으면 located=false이고 인증 대상이 아니다', async () => {
    const { data } = await call<LocationDetail>('GET', '/locations/1')
    expect(data.located).toBe(false)
    expect(data.lat).toBeNull()
    expect(data.directions_url).toBeNull()
    expect(data.cert).toMatchObject({ eligible: false, excluded_reason: 'no_coordinates' })
  })

  it('없는 촬영지는 LOCATION_NOT_FOUND', async () => {
    const r = await call<{ error: { code: string } }>('GET', '/locations/999')
    expect(r.status).toBe(404)
    expect(r.data.error.code).toBe('LOCATION_NOT_FOUND')
  })
})
