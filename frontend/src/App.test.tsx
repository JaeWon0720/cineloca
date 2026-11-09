import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import App from './App.tsx'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

describe('App 라우팅 (SRS 6.1)', () => {
  it('하단 탭 5개를 순서대로 보여 준다', () => {
    renderAt('/')

    const nav = screen.getByRole('navigation', { name: '주요 메뉴' })
    const labels = within(nav)
      .getAllByRole('link')
      .map((link) => link.textContent)

    expect(labels).toEqual(['지도', '작품', 'AI 탐색', '코스', '내 기록'])
  })

  it.each([
    ['/', '지도'],
    ['/works', '작품'],
    ['/ask', 'AI 탐색'],
    ['/courses', '코스'],
    ['/me', '내 기록'],
    ['/sources', '데이터 출처'],
  ])('%s 주소에서 "%s" 화면을 보여 준다', (path, title) => {
    renderAt(path)

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(title)
  })

  it('현재 탭에 aria-current="page"를 붙인다', () => {
    renderAt('/works')

    const nav = screen.getByRole('navigation', { name: '주요 메뉴' })

    expect(within(nav).getByRole('link', { name: '작품' }).getAttribute('aria-current')).toBe('page')
    expect(within(nav).getByRole('link', { name: '지도' }).getAttribute('aria-current')).toBeNull()
  })

  it('관리자 화면에는 하단 탭이 없다', () => {
    renderAt('/admin')

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('관리자')
    expect(screen.queryByRole('navigation', { name: '주요 메뉴' })).toBeNull()
  })

  it('없는 주소에서는 안내 화면을 보여 준다', () => {
    renderAt('/no-such-page')

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('페이지를 찾을 수 없습니다')
  })
})
