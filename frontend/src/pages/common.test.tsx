// 공통 화면 테스트: 첫 방문 이용 안내(FR-701), 데이터 출처 화면(UI-09, FR-705)
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import App from '../App.tsx'
import { NOTICE_VERSION } from '../api/notice.ts'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

describe('이용 안내 (FR-701)', () => {
  it('첫 방문에 안내 창을 띄우고, 확인하면 닫고 버전을 저장한다', () => {
    renderAt('/works')
    const dialog = screen.getByRole('dialog', { name: '이용 안내' })
    fireEvent.click(within(dialog).getByRole('button', { name: '확인했어요' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(localStorage.getItem('cineloca:notice')).toBe(JSON.stringify(NOTICE_VERSION))
  })

  it('확인한 뒤에는 다시 띄우지 않는다', () => {
    localStorage.setItem('cineloca:notice', JSON.stringify(NOTICE_VERSION))
    renderAt('/works')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('안내 버전이 바뀌면 다시 띄운다', () => {
    localStorage.setItem('cineloca:notice', JSON.stringify('2000-01'))
    renderAt('/works')
    expect(screen.getByRole('dialog', { name: '이용 안내' })).toBeTruthy()
  })
})

describe('데이터 출처 (UI-09)', () => {
  beforeEach(() => localStorage.setItem('cineloca:notice', JSON.stringify(NOTICE_VERSION)))

  it('출처 대장을 표로 보여 준다', async () => {
    renderAt('/sources')
    expect(screen.getByRole('heading', { level: 1, name: '데이터 출처' })).toBeTruthy()
    expect((await screen.findAllByText('메트로신문')).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('row').length).toBeGreaterThan(2)
    expect(screen.getAllByRole('link').some((a) => a.getAttribute('href')?.startsWith('http'))).toBe(true)
  })

  it('이용 안내를 다시 볼 수 있다', async () => {
    renderAt('/sources')
    fireEvent.click(screen.getByRole('button', { name: '이용 안내 다시 보기' }))
    expect(screen.getByRole('dialog', { name: '이용 안내' })).toBeTruthy()
  })
})
