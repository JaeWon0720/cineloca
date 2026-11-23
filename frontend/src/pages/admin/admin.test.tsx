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

    expect(await screen.findByText('로그아웃했어요.')).toBeTruthy()
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

describe('UI-10 동기화 리포트 (API-021, FR-612·613·616)', () => {
  beforeEach(() => resetMockAdmin())

  async function openSync() {
    renderAt('/admin/sync')
    await login(MOCK_ADMIN_LOGIN)
    await screen.findByRole('heading', { level: 3, name: /#12/ })
  }

  it('최근 리포트를 최신 먼저 보여 주고, 가드 중단·경고·실패를 드러낸다', async () => {
    await openSync()
    const heads = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(heads).toEqual(['#12 수동', '#11 정기', '#10 정기'])
    expect(screen.getByText('가드로 중단')).toBeTruthy()
    expect(screen.getByText(/촬영지 수가 직전보다 53% 줄었습니다/)).toBeTruthy()
    expect(screen.getByText(/장르 묶음표에 없는 KMDb 장르 1종/)).toBeTruthy()
    expect(screen.getByText('실패 1건 보기')).toBeTruthy()
    // 직전 대비 증감: 작품 431 → 436
    expect(screen.getAllByText('+5').length).toBeGreaterThan(0)
  })

  it('오늘 호출 수가 한도의 70%를 넘으면 표시한다 (NFR-005)', async () => {
    await openSync()
    expect(screen.getByText('182 / 250')).toBeTruthy()
    expect(screen.getByText('70% 넘음')).toBeTruthy()
    expect(screen.getByText('7일마다')).toBeTruthy()
  })

  it('지금 동기화를 요청하면 대기 중으로 바뀌고 버튼이 잠긴다', async () => {
    await openSync()
    fireEvent.click(screen.getByRole('button', { name: '지금 동기화' }))

    expect(await screen.findByText('동기화를 요청했습니다. 1분 안에 시작됩니다. (실행 #13)')).toBeTruthy()
    expect(await screen.findByText(/동기화 #13\(수동\) 대기 중/)).toBeTruthy()
    expect((screen.getByRole('button', { name: '지금 동기화' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('세션이 끝난 뒤 관리 API를 부르면 로그인 화면으로 돌아간다', async () => {
    await openSync()
    expireMockAdmin()
    fireEvent.click(screen.getByRole('button', { name: '새로 고침' }))

    expect(await screen.findByRole('heading', { level: 1, name: '관리자 로그인' })).toBeTruthy()
    expect(screen.getByText('로그인 시간이 지나 다시 로그인해 주세요.')).toBeTruthy()
  })
})

describe('UI-11 검수 큐 (API-022, FR-608·609, BR-04)', () => {
  beforeEach(() => resetMockAdmin())

  async function openQueue() {
    renderAt('/admin/review')
    await login(MOCK_ADMIN_LOGIN)
    await screen.findByRole('link', { name: '대외비 (2023)' })
  }

  it('대기 항목을 종류·제안자와 함께 보여 준다', async () => {
    await openQueue()
    expect(screen.getByRole('button', { name: '대기 8' })).toBeTruthy()
    expect(screen.getAllByRole('listitem').filter((li) => li.classList.contains('review-item')).length).toBe(8)
    expect(screen.getByText(/AI 판단: KMDb에 없음/)).toBeTruthy()
    expect(screen.getByText(/AI가 답하지 못해 제안이 없어요/)).toBeTruthy()
  })

  it('승인하면 처리됨으로 옮겨 간다', async () => {
    await openQueue()
    const row = screen.getByRole('link', { name: '대외비 (2023)' }).closest('li')!
    fireEvent.click(within(row).getByRole('button', { name: '승인' }))

    expect(await screen.findByText('승인했어요: 대외비')).toBeTruthy()
    expect(await screen.findByRole('button', { name: '대기 7' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '처리됨 1' }))
    expect(await screen.findByText(/^승인 /)).toBeTruthy()
  })

  it('제안 태그가 없는 항목은 승인할 수 없고, 수정으로 태그를 골라 승인한다', async () => {
    await openQueue()
    const row = screen.getByRole('link', { name: '구 동부산대학교' }).closest('li')!
    expect((within(row).getByRole('button', { name: '승인' }) as HTMLButtonElement).disabled).toBe(true)

    fireEvent.click(within(row).getByRole('button', { name: '수정' }))
    const save = within(row).getByRole('button', { name: '고친 태그로 승인' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    fireEvent.click(within(row).getByLabelText('학교'))
    fireEvent.click(save)
    expect(await screen.findByText('수정 승인했어요: 구 동부산대학교')).toBeTruthy()
  })

  it('매칭 수정은 KMDb 번호 형식(K+숫자 5자리)일 때만 보낸다 (경계)', async () => {
    await openQueue()
    const row = screen.getByRole('link', { name: '해운대 연인들 (2012)' }).closest('li')!
    fireEvent.click(within(row).getByRole('button', { name: '수정' }))
    const input = within(row).getByLabelText(/연결할 KMDb 번호/)
    const save = within(row).getByRole('button', { name: '고친 번호로 승인' }) as HTMLButtonElement
    fireEvent.change(input, { target: { value: 'K1234' } })
    expect(save.disabled).toBe(true)
    fireEvent.change(input, { target: { value: 'k12345' } })
    expect(save.disabled).toBe(false)
    fireEvent.click(save)
    expect(await screen.findByText('수정 승인했어요: 해운대 연인들')).toBeTruthy()
  })

  it('반려하면 대기에서 빠진다', async () => {
    await openQueue()
    const row = screen.getByRole('link', { name: '친구 (2001)' }).closest('li')!
    fireEvent.click(within(row).getByRole('button', { name: '반려' }))
    expect(await screen.findByText('반려했어요: 친구')).toBeTruthy()
    expect(screen.queryByRole('link', { name: '친구 (2001)' })).toBeNull()
  })
})

describe('UI-11 촬영지 속성 편집 (API-023, FR-610, BR-06·07)', () => {
  beforeEach(() => resetMockAdmin())

  async function openAttrs(id: number) {
    renderAt(`/admin/review?tab=attrs&id=${id}`)
    await login(MOCK_ADMIN_LOGIN)
    await screen.findByRole('heading', { level: 2, name: /#\d+/ })
  }

  it('인증 반경은 50~300m만 받는다 (경계: 49·50·300·301)', async () => {
    await openAttrs(5)
    const radius = screen.getByLabelText(/인증 반경/)
    const save = screen.getByRole('button', { name: '저장' }) as HTMLButtonElement
    for (const [v, okv] of [['49', false], ['50', true], ['300', true], ['301', false]] as const) {
      fireEvent.change(radius, { target: { value: v } })
      expect(!!screen.queryByText('인증 반경은 50~300m 사이 정수예요.')).toBe(!okv)
    }
    fireEvent.change(radius, { target: { value: '150' } })
    expect(save.disabled).toBe(false)
    fireEvent.click(save)
    expect(await screen.findByText('저장했어요.')).toBeTruthy()
    expect((screen.getByLabelText(/인증 반경/) as HTMLInputElement).value).toBe('150')
  })

  it('주의 구분을 고르면 인증 대상에서 빠진다고 알리고 저장한다', async () => {
    await openAttrs(5)
    expect(screen.getByText(/반경 100m 안에서 방문 인증할 수 있어요/)).toBeTruthy()
    fireEvent.click(screen.getByLabelText('사유지'))
    expect(screen.getByText(/방문 인증 대상이 아니에요/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText(/안전 안내/), { target: { value: '사유지라 안쪽은 들어가지 마세요' } })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(await screen.findByText('저장했어요.')).toBeTruthy()
    expect(screen.getByText('안전 안내 (17/200자)')).toBeTruthy()
  })

  it('없는 촬영지 번호는 안내한다', async () => {
    renderAt('/admin/review?tab=attrs&id=9999')
    await login(MOCK_ADMIN_LOGIN)
    expect((await screen.findByRole('alert')).textContent).toBe('촬영지를 찾을 수 없습니다.')
  })

  it('검수 큐의 태그 항목에서 그 촬영지 속성 편집으로 간다', async () => {
    renderAt('/admin/review')
    await login(MOCK_ADMIN_LOGIN)
    const row = (await screen.findByRole('link', { name: '온천천' })).closest('li')!
    fireEvent.click(within(row).getByRole('link', { name: '이 촬영지 속성 편집' }))
    expect(await screen.findByRole('heading', { level: 2, name: /온천천/ })).toBeTruthy()
  })
})

