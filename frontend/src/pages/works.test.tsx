// F1 화면 테스트: 작품 목록(UI-03), 작품 상세(UI-04)
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  localStorage.setItem('cineloca:notice', JSON.stringify(NOTICE_VERSION))
})

describe('작품 목록 (UI-03)', () => {
  it('작품을 보여 주고, 누르면 상세로 간다', async () => {
    renderAt('/works')
    fireEvent.click(await screen.findByRole('link', { name: /리바운드.*부산 촬영지/ }))
    expect(await screen.findByRole('heading', { level: 1, name: /리바운드/ })).toBeTruthy()
  })

  it('검색 결과가 없으면 안내한다', async () => {
    renderAt('/works')
    fireEvent.change(screen.getByRole('searchbox', { name: '작품 검색' }), { target: { value: '없는작품이름' } })
    expect(await screen.findByText('검색 결과가 없어요.')).toBeTruthy()
  })

  it('장르로 거른다', async () => {
    renderAt('/works')
    await screen.findByRole('link', { name: /리바운드.*부산 촬영지/ })
    fireEvent.change(screen.getByRole('combobox', { name: '장르' }), { target: { value: '사극' } })
    await waitFor(() => expect(screen.queryByRole('link', { name: /리바운드.*부산 촬영지/ })).toBeNull())
    expect(screen.getByRole('link', { name: /군도.*부산 촬영지/ })).toBeTruthy()
  })
})

describe('작품 상세 (UI-04)', () => {
  it('부산 촬영지 목록이 촬영지 상세로 이어진다', async () => {
    renderAt('/works/1')
    expect(await screen.findByRole('heading', { level: 2, name: /부산 촬영지 \d+곳/ })).toBeTruthy()
    expect(screen.getByRole('link', { name: /영도대교/ }).getAttribute('href')).toBe('/locations/4')
  })

  it('KMDb에 없는 작품은 작품명과 촬영지만 보여 준다 (UC-01)', async () => {
    renderAt('/works/1')
    expect(await screen.findByText(/촬영지 정보 제공 작품/)).toBeTruthy()
    expect(screen.queryByText('관람등급')).toBeNull()
    expect(screen.queryByText(/장항준/)).toBeNull()
  })

  it('KMDb 작품은 감독·출연(최대 5명)·등급을 보이고, 값이 없으면 정보 없음으로 쓴다 (FR-103·107)', async () => {
    renderAt('/works/6')
    expect(await screen.findByRole('heading', { level: 1, name: '1987' })).toBeTruthy()
    expect(screen.getByText('장준환')).toBeTruthy()
    expect(screen.getByText('관람등급').nextElementSibling?.textContent).toBe('정보 없음')
    const cast = screen.getByText('출연').nextElementSibling?.textContent ?? ''
    expect(cast.split(', ').length).toBeLessThanOrEqual(5)
    expect(screen.getByRole('img', { name: '1987 포스터' })).toBeTruthy() // 포스터가 없으면 기본 그림
    expect(screen.queryByRole('link', { name: '예고편 보기' })).toBeNull() // 예고편이 없으면 영역을 숨긴다
  })

  it('없는 작품은 오류를 보여 준다', async () => {
    renderAt('/works/999')
    expect((await screen.findByRole('alert')).textContent).toContain('작품을 찾을 수 없습니다')
  })
})
