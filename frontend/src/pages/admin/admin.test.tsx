// 관리자 화면 틀 (UI-10~12 공통, FR-615, API-020): 로그인·잠금·메뉴·로그아웃·세션 만료
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '../../api/client.ts'
import App from '../../App.tsx'
import { isAdminRequired } from '../../lib/admin.ts'
import { MOCK_ADMIN_LOGIN } from '../../mocks/adminLogin.ts'
import { expireMockAdmin, resetMockAdmin } from '../../mocks/handlers/admin.ts'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

async function login(password: string) {
  const input = await screen.findByLabelText('관리자 비밀번호')
  fireEvent.change(input, { target: { value: password } })
  fireEvent.click(screen.getByRole('button', { name: '로그인' }))
}

describe('관리자 화면 틀 (API-020, FR-615)', () => {
  beforeEach(() => resetMockAdmin())

  it('로그인하면 관리 메뉴 3개가 보이고 동기화 리포트로 들어간다', async () => {
    renderAt('/admin')
    await login(MOCK_ADMIN_LOGIN)

    expect(await screen.findByRole('heading', { level: 1, name: '동기화 리포트' })).toBeTruthy()
    const nav = screen.getByRole('navigation', { name: '관리 메뉴' })
    const labels = within(nav)
      .getAllByRole('link')
      .map((a) => a.textContent)
    expect(labels).toEqual(['동기화 리포트UI-10', '검수 큐·속성 편집UI-11', '추천 코스 관리UI-12', '사용자 화면으로'])
    expect(within(nav).getByRole('link', { name: /동기화 리포트/ }).getAttribute('aria-current')).toBe('page')
    expect(screen.getByText(/까지 로그인 유지$/)).toBeTruthy()
  })

  it('메뉴를 누르면 그 관리 화면으로 바뀐다', async () => {
    renderAt('/admin/review')
    await login(MOCK_ADMIN_LOGIN)

    expect(await screen.findByRole('heading', { level: 1, name: '검수 큐·속성 편집' })).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: /추천 코스 관리/ }))
    expect(await screen.findByRole('heading', { level: 1, name: '추천 코스 관리' })).toBeTruthy()
  })

  it('비밀번호가 틀리면 남은 시도를 알려 주고 칸을 비운다', async () => {
    renderAt('/admin')
    await login('wrong')

    expect((await screen.findByRole('alert')).textContent).toBe('비밀번호가 맞지 않습니다. 남은 시도 4회')
    expect((screen.getByLabelText('관리자 비밀번호') as HTMLInputElement).value).toBe('')
    expect(screen.queryByRole('navigation', { name: '관리 메뉴' })).toBeNull()
  })

  it('5회 연속 틀리면 10분 잠금을 알리고, 맞는 비밀번호도 받지 않는다 (경계: 4회째는 남은 1회)', async () => {
    renderAt('/admin')
    for (let i = 1; i <= 4; i++) {
      await login(`wrong-${i}`)
      expect((await screen.findByRole('alert')).textContent).toBe(`비밀번호가 맞지 않습니다. 남은 시도 ${5 - i}회`)
    }
    await login('wrong-5')
    expect((await screen.findByRole('alert')).textContent).toBe('비밀번호를 5회 틀려 10분 동안 로그인할 수 없습니다. 10분 뒤에 다시 시도해 주세요.')

    await login(MOCK_ADMIN_LOGIN)
    expect((await screen.findByRole('alert')).textContent).toMatch(/10분 동안 로그인할 수 없습니다/)
    expect(screen.queryByRole('navigation', { name: '관리 메뉴' })).toBeNull()
  })

  it('로그아웃하면 로그인 화면으로 돌아간다', async () => {
    renderAt('/admin')
    await login(MOCK_ADMIN_LOGIN)
    fireEvent.click(await screen.findByRole('button', { name: '로그아웃' }))

    expect((await screen.findByRole('status')).textContent).toBe('로그아웃했어요.')
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('관리자 로그인')
  })

  it('이미 로그인돼 있으면 다시 묻지 않고, 세션이 끝났으면 로그인부터 묻는다', async () => {
    const first = renderAt('/admin')
    await login(MOCK_ADMIN_LOGIN)
    await screen.findByRole('navigation', { name: '관리 메뉴' })
    first.unmount()

    const again = renderAt('/admin/sync')
    expect(await screen.findByRole('heading', { level: 1, name: '동기화 리포트' })).toBeTruthy()
    again.unmount()

    expireMockAdmin()
    renderAt('/admin/sync')
    expect(await screen.findByRole('heading', { level: 1, name: '관리자 로그인' })).toBeTruthy()
  })

  it('관리 API의 401 ADMIN_REQUIRED만 세션 만료로 본다', () => {
    expect(isAdminRequired(new ApiError(401, 'ADMIN_REQUIRED', ''))).toBe(true)
    expect(isAdminRequired(new ApiError(401, 'ADMIN_AUTH_FAILED', ''))).toBe(false)
    expect(isAdminRequired(new ApiError(403, 'NOTICE_REQUIRED', ''))).toBe(false)
  })
})
