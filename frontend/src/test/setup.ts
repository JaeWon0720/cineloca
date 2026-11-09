// Vitest 공통 설정: 테스트마다 렌더링한 화면을 정리한다.
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})
