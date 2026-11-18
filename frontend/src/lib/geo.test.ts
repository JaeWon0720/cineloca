import { describe, expect, it } from 'vitest'
import { fmtDist, haversineM, stationLabel } from './geo.ts'

describe('거리 표시와 계산', () => {
  it('거리를 m·km로 바꾼다', () => {
    expect(fmtDist(850)).toBe('850m')
    expect(fmtDist(1520)).toBe('1.5km')
  })

  it('역 이름에 "역"을 한 번만 붙인다', () => {
    expect(stationLabel('남포')).toBe('남포역')
    expect(stationLabel('부산역')).toBe('부산역')
  })

  it('부산역~남포역 직선거리는 약 2km다', () => {
    const d = haversineM(35.115, 129.041, 35.098, 129.034)
    expect(d).toBeGreaterThan(1800)
    expect(d).toBeLessThan(2200)
  })
})
