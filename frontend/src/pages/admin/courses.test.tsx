// UI-12 추천 코스 관리 (API-024, FR-614): 후보 만들기·수정·승인·승인 취소·삭제
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import App from '../../App.tsx'
import { MOCK_ADMIN_LOGIN } from '../../mocks/adminLogin.ts'
import { resetMockAdmin } from '../../mocks/handlers/admin.ts'
import { resetMockAdminCourses } from '../../mocks/handlers/adminCourses.ts'

async function open() {
  render(
    <MemoryRouter initialEntries={['/admin/courses']}>
      <App />
    </MemoryRouter>,
  )
  fireEvent.change(await screen.findByLabelText('관리자 비밀번호'), { target: { value: MOCK_ADMIN_LOGIN } })
  fireEvent.click(screen.getByRole('button', { name: '로그인' }))
  return screen.findByRole('list', { name: '추천 코스 목록' })
}

const rows = () =>
  within(screen.getByRole('list', { name: '추천 코스 목록' }))
    .getAllByRole('listitem')
    .map((li) => li.textContent)

describe('UI-12 추천 코스 관리 (API-024, FR-614)', () => {
  beforeEach(() => {
    resetMockAdmin()
    resetMockAdminCourses()
  })

  it('목록에 승인 상태와 총 시간을 보여 주고, 상태로 거른다', async () => {
    await open()
    expect(rows()).toHaveLength(2)
    expect(rows()[0]).toMatch(/원도심 영화 골목 걷기지역승인됨2곳/)
    fireEvent.click(screen.getByRole('button', { name: '승인 전' }))
    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(rows()[0]).toMatch(/「1987」 촬영지 코스/)
  })

  it('지역 테마로 후보를 만들면 승인 전 코스가 열린다', async () => {
    await open()
    const form = screen.getByRole('form', { name: '후보 만들기' })
    await waitFor(() => expect(within(form).getAllByRole('option', { name: '해운대구' }).length).toBe(1))
    fireEvent.change(within(form).getByLabelText('구·군'), { target: { value: '해운대구' } })
    fireEvent.change(within(form).getByLabelText('촬영지 수'), { target: { value: '3' } })
    fireEvent.click(within(form).getByRole('button', { name: '후보 만들기' }))

    const editor = await screen.findByRole('article', { name: '코스 편집' })
    expect((within(editor).getByLabelText('제목') as HTMLInputElement).value).toBe('해운대구 촬영지 코스')
    expect(within(editor).getAllByRole('listitem')).toHaveLength(3)
    expect(within(editor).getByText('승인 전')).toBeTruthy()
    await waitFor(() => expect(rows()).toHaveLength(3))
  })

  it('순서·체류를 고쳐 저장하고 승인한 뒤, 승인 취소와 삭제를 한다', async () => {
    await open()
    fireEvent.click(screen.getByRole('button', { name: '「1987」 촬영지 코스' }))
    const editor = await screen.findByRole('article', { name: '코스 편집' })
    const names = () => within(editor).getAllByRole('listitem').map((li) => li.querySelector('.stop-name')!.textContent)
    await waitFor(() => expect(names()[0]).toMatch(/^동래별장/))

    fireEvent.click(within(editor).getByRole('button', { name: '동래별장 아래로' }))
    fireEvent.change(within(editor).getAllByLabelText('체류(분)')[0], { target: { value: '40' } })
    expect(within(editor).getByText(/승인된 코스는 승인 전으로 돌아가요/)).toBeTruthy()
    fireEvent.click(within(editor).getByRole('button', { name: '저장하고 승인' }))

    expect(await within(editor).findByText('승인했어요. 사용자 추천 코스에 나옵니다.')).toBeTruthy()
    expect(names()[0]).toMatch(/^해운정사/)
    expect((within(editor).getAllByLabelText('체류(분)')[0] as HTMLInputElement).value).toBe('40')
    expect(within(editor).queryByRole('button', { name: '후보 삭제' })).toBeNull()

    fireEvent.click(within(editor).getByRole('button', { name: '승인 취소' }))
    expect(await within(editor).findByText('승인을 취소했어요.')).toBeTruthy()
    fireEvent.click(within(editor).getByRole('button', { name: '후보 삭제' }))
    await waitFor(() => expect(screen.queryByRole('article', { name: '코스 편집' })).toBeNull())
    await waitFor(() => expect(rows()).toHaveLength(1))
  })

  it('2곳 밑으로는 뺄 수 없고, 좌표 없는 촬영지를 더하면 서버 안내를 보여 준다', async () => {
    await open()
    fireEvent.click(screen.getByRole('button', { name: '원도심 영화 골목 걷기' }))
    const editor = await screen.findByRole('article', { name: '코스 편집' })
    await waitFor(() => expect(within(editor).getAllByRole('listitem')).toHaveLength(2))
    expect((within(editor).getAllByRole('button', { name: /빼기$/ })[0] as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(within(editor).getByLabelText('촬영지 번호로 더하기'), { target: { value: '1' } })
    fireEvent.click(within(editor).getByRole('button', { name: '더하기' }))
    fireEvent.click(within(editor).getByRole('button', { name: '저장' }))
    expect(await within(editor).findByText('위치가 확인되지 않은 촬영지는 코스에 넣을 수 없어요.')).toBeTruthy()
  })
})
