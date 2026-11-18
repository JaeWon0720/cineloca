import { describe, expect, it } from 'vitest'
import { withRetryHint } from './client.ts'

describe('오류 문장의 다시 시도 안내', () => {
  it('retry_after가 있으면 몇 초(분) 뒤에 다시 하라고 붙인다', () => {
    expect(withRetryHint('요청이 너무 많습니다.', 30)).toBe('요청이 너무 많습니다. 30초 뒤에 다시 시도해 주세요.')
    expect(withRetryHint('요청이 너무 많습니다.', 90)).toBe('요청이 너무 많습니다. 2분 뒤에 다시 시도해 주세요.')
  })

  it('retry_after가 없으면 문장을 그대로 둔다', () => {
    expect(withRetryHint('촬영지를 찾을 수 없습니다', null)).toBe('촬영지를 찾을 수 없습니다')
    expect(withRetryHint('촬영지를 찾을 수 없습니다', 0)).toBe('촬영지를 찾을 수 없습니다')
  })
})
