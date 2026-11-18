// F2 화면 테스트: 지도 홈(UI-01), 촬영지 상세(UI-02)
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

// 지도 마커도 버튼 역할이라, 목록 안의 카드만 찾는다
const list = () => within(document.querySelector<HTMLElement>('.list')!)

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('cineloca:notice', JSON.stringify(NOTICE_VERSION))
})

describe('지도 홈 (UI-01)', () => {
  it('위치를 못 얻으면 부산역 기준으로 가까운 촬영지를 보여 준다', async () => {
    renderAt('/')
    expect(await screen.findByText(/부산역 기준 1km 안/)).toBeTruthy()
    expect(await screen.findByRole('heading', { level: 2, name: /반경 안 촬영지 [1-9]\d*곳/ })).toBeTruthy()
  })

  it('결과가 없으면 반경을 넓히라고 안내하고, 누르면 넓힌다', async () => {
    renderAt('/')
    fireEvent.change(await screen.findByRole('combobox', { name: '기준 위치' }), { target: { value: 'gu:북구' } })
    fireEvent.click(await screen.findByRole('button', { name: '반경을 3km로 넓히기' }))
    expect(await screen.findByText(/북구 기준 3km 안/)).toBeTruthy()
  })

  it('목록에서 고르면 요약 카드에 자세히 보기와 길찾기 링크가 나온다', async () => {
    renderAt('/')
    fireEvent.change(await screen.findByRole('combobox', { name: '기준 위치' }), { target: { value: 'gu:영도구' } })
    fireEvent.click(screen.getByRole('button', { name: '3km' }))
    fireEvent.click(await list().findByRole('button', { name: /영도대교/ }))
    expect(screen.getByRole('link', { name: '자세히 보기' }).getAttribute('href')).toBe('/locations/4')
    expect(screen.getByRole('link', { name: '카카오맵 길찾기' }).getAttribute('href')).toContain('map.kakao.com/link/to/')
  })

  it('구·군 필터를 걸면 그 구·군 촬영지만 남는다', async () => {
    renderAt('/')
    fireEvent.click(await screen.findByRole('button', { name: '3km' }))
    await list().findByRole('button', { name: /코모도호텔/ })
    fireEvent.click(screen.getByRole('button', { name: '필터' }))
    fireEvent.change(screen.getByRole('combobox', { name: '구·군 필터' }), { target: { value: '동구' } })
    await waitFor(() => expect(list().queryByRole('button', { name: /코모도호텔/ })).toBeNull())
    expect(list().getByRole('button', { name: /정란각/ })).toBeTruthy()
  })

  it('개봉연도 구간을 모두 만족하는 곳만 남긴다', async () => {
    renderAt('/')
    fireEvent.click(await screen.findByRole('button', { name: '3km' }))
    await list().findByRole('button', { name: /코모도호텔/ })
    fireEvent.click(screen.getByRole('button', { name: '필터' }))
    fireEvent.change(screen.getByRole('combobox', { name: '개봉연도 시작' }), { target: { value: '2023' } })
    fireEvent.change(screen.getByRole('combobox', { name: '개봉연도 끝' }), { target: { value: '2023' } })
    await waitFor(() => expect(list().queryByRole('button', { name: /정란각/ })).toBeNull())
    expect(list().getByRole('button', { name: /코모도호텔/ })).toBeTruthy()
  })

  it('필터는 접혀 있고, 건 필터 수를 보여 주며 한 번에 지울 수 있다', async () => {
    renderAt('/')
    const toggle = await screen.findByRole('button', { name: '필터' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('combobox', { name: '장르' })).toBeNull()
    fireEvent.click(toggle)
    fireEvent.change(screen.getByRole('combobox', { name: '구·군 필터' }), { target: { value: '동구' } })
    expect(screen.getByRole('button', { name: '필터1' }).getAttribute('aria-expanded')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: '필터 지우기' }))
    expect((screen.getByRole('combobox', { name: '구·군 필터' }) as HTMLSelectElement).value).toBe('')
  })

  it('장르를 고르면 장르 정보가 있는 작품만 보인다고 알린다', async () => {
    renderAt('/')
    fireEvent.click(await screen.findByRole('button', { name: '필터' }))
    expect(screen.getByText('장르 정보가 있는 작품의 촬영지만 보여 줍니다.')).toBeTruthy()
    const genre = screen.getByRole('combobox', { name: '장르' }) as HTMLSelectElement
    fireEvent.change(genre, { target: { value: genre.options[1].value } })
    expect(await screen.findByText(/적용 중: 장르 정보가 있는 작품의 촬영지만/)).toBeTruthy()
  })

  it('목록 번호는 거리순이고, 고른 촬영지는 영화표 모양 요약에 나온다', async () => {
    renderAt('/')
    fireEvent.click(await screen.findByRole('button', { name: '3km' }))
    const first = await list().findAllByRole('button')
    fireEvent.click(first[0])
    const ticket = screen.getByRole('article')
    expect(ticket.getAttribute('aria-label')).toMatch(/요약$/)
  })
})

describe('촬영지 상세 (UI-02)', () => {
  it('주소·촬영 작품·길찾기·출처를 보여 준다', async () => {
    renderAt('/locations/4')
    expect(await screen.findByRole('heading', { level: 1, name: '영도대교' })).toBeTruthy()
    expect(screen.getByRole('link', { name: '카카오맵 길찾기' })).toBeTruthy()
    expect(screen.getAllByRole('link', { name: /리바운드/ }).some((a) => a.getAttribute('href') === '/works/1')).toBe(true)
    expect(screen.getByText(/출처 \d+건/)).toBeTruthy()
  })

  it('좌표가 없는 곳은 지도와 길찾기 대신 위치 미확인을 알린다', async () => {
    renderAt('/locations/1')
    expect(await screen.findByText(/위치 미확인/)).toBeTruthy()
    expect(screen.queryByRole('link', { name: '카카오맵 길찾기' })).toBeNull()
    expect(screen.getAllByRole('note').some((n) => n.textContent?.includes('외부에서만 관람'))).toBe(true)
    expect(screen.getByRole('img', { name: /사진 없음/ })).toBeTruthy()
  })

  it('없는 촬영지는 오류를 보여 준다', async () => {
    renderAt('/locations/999')
    expect((await screen.findByRole('alert')).textContent).toContain('촬영지를 찾을 수 없습니다')
  })
})
