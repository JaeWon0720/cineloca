// 스프린트 2 화면 테스트: F1 AI 작품 소개(UI-04), F3 AI 탐색(UI-05), F4 코스(UI-06·07), F5 방문 인증·스탬프북(UI-02·08).
// 경계값(200자, 정확도 50m, 인증 반경, 코스 2~10곳, 저장 10개, 인증 대상 2곳)을 함께 본다.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App.tsx'
import { api, ApiError } from '../api/client.ts'
import { NOTICE_VERSION } from '../api/notice.ts'
import { aiErrorMessage, queryErrorMessage } from '../lib/ai.ts'
import { stampMessage } from '../lib/stamp.ts'
import { MAX_SAVED_COURSES, savedCourses, tokens } from '../lib/wallet.ts'
import { mockRequest } from '../mocks/server.ts'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

/** 브라우저 위치를 정해 둔다(위도·경도·정확도 m) */
function setPosition(lat: number, lon: number, accuracy: number) {
  Object.defineProperty(navigator, 'geolocation', {
    configurable: true,
    value: {
      getCurrentPosition: (ok: PositionCallback) =>
        ok({ coords: { latitude: lat, longitude: lon, accuracy } } as unknown as GeolocationPosition),
    },
  })
}

// 영도대교(촬영지 4): 인증 반경 100m
const BRIDGE = { id: 4, lat: 35.0948, lon: 129.0368 }
const M_PER_DEG_LAT = 111195 // 위도 1도 ≈ 111.195km

async function stampDirect(id: number, lat: number, lon: number) {
  const r = await mockRequest({ method: 'POST', path: '/stamps', body: { location_id: id, lat, lon, accuracy_m: 10, held: tokens.all() }, headers: {} })
  tokens.add((r.data as { token: string }).token)
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('cineloca:notice', JSON.stringify(NOTICE_VERSION))
})
afterEach(() => vi.restoreAllMocks())

// ---------------------------------------------------------------- F1
describe('AI 작품 소개 (UI-04, FR-108~111)', () => {
  it('스포일러 단계 기본값은 없음이고, 소개 문장에 AI 생성 표시를 붙인다', async () => {
    renderAt('/works/6')
    const none = await screen.findByRole('radio', { name: '스포일러 없음' })
    expect(none.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: 'AI 소개 보기' }))
    expect(await screen.findByText('AI 생성')).toBeTruthy()
    expect(screen.getByText(/2017년에 개봉한 장준환 감독/)).toBeTruthy()
  })

  it('일부 단계로 바꾸면 다시 요청하고, 근거와 맞지 않아 뺀 문장 수를 알린다 (FR-110)', async () => {
    renderAt('/works/6')
    fireEvent.click(await screen.findByRole('button', { name: 'AI 소개 보기' }))
    await screen.findByText('AI 생성')
    fireEvent.click(screen.getByRole('radio', { name: '일부 포함' }))
    expect(await screen.findByText(/근거와 맞지 않는 문장 1개는 뺐어요/)).toBeTruthy()
  })

  it('KMDb에 연결되지 않은 작품은 AI 소개를 주지 않는다 (FR-108)', async () => {
    renderAt('/works/1')
    expect(await screen.findByText('KMDb와 연결된 작품만 AI 소개를 제공해요.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'AI 소개 보기' })).toBeNull()
  })

  it('이용 안내를 확인하지 않아 거절되면 안내 창을 다시 띄운다 (FR-701)', () => {
    const show = vi.fn()
    const msg = aiErrorMessage(new ApiError(403, 'NOTICE_REQUIRED', '이용 안내를 확인한 뒤 다시 시도해 주세요.'), show)
    expect(show).toHaveBeenCalledOnce()
    expect(msg).toContain('이용 안내')
  })
})

// ---------------------------------------------------------------- F3
describe('AI 탐색 (UI-05, FR-301~312)', () => {
  it('예시 질문을 3개 이상 보여 준다 (FR-301)', async () => {
    renderAt('/ask')
    const ex = await screen.findByRole('generic', { name: '예시 질문' }).catch(() => screen.getByLabelText('예시 질문'))
    expect(within(ex).getAllByRole('button').length).toBeGreaterThanOrEqual(4) // 예시 3개 + 필터로 찾기
  })

  it('200자는 보낼 수 있고 201자는 막는다 (FR-301 경계)', async () => {
    renderAt('/ask')
    const box = await screen.findByRole('textbox', { name: '질문' })
    const send = screen.getByRole('button', { name: '묻기' }) as HTMLButtonElement
    fireEvent.change(box, { target: { value: '가'.repeat(200) } })
    expect(send.disabled).toBe(false)
    expect(screen.getByText('200/200')).toBeTruthy()
    fireEvent.change(box, { target: { value: '가'.repeat(201) } })
    expect(send.disabled).toBe(true)
    expect(screen.getByText(/200자까지 쓸 수 있어요/)).toBeTruthy()
  })

  it('해석한 조건을 칩으로 보여 주고, 결과·AI 요약을 낸다 (FR-306·307·312)', async () => {
    renderAt('/ask')
    fireEvent.change(await screen.findByRole('textbox', { name: '질문' }), { target: { value: '해운대에서 찍은 영화 알려 줘' } })
    fireEvent.click(screen.getByRole('button', { name: '묻기' }))
    const chips = await screen.findByRole('list', { name: '해석한 조건' })
    expect(within(chips).getByText('구·군: 해운대구')).toBeTruthy()
    expect(screen.getByText('AI 생성')).toBeTruthy()
    expect(screen.getByRole('heading', { name: /촬영지 \d+곳/ })).toBeTruthy()
  })

  it('칩을 지우면 그 조건을 뺀 결과를 다시 보여 준다 (FR-312)', async () => {
    renderAt('/ask')
    fireEvent.change(await screen.findByRole('textbox', { name: '질문' }), { target: { value: '해운대에서 찍은 영화' } })
    fireEvent.click(screen.getByRole('button', { name: '묻기' }))
    fireEvent.click(await screen.findByRole('button', { name: '구·군: 해운대구 조건 빼기' }))
    await waitFor(() => expect(screen.queryByText('구·군: 해운대구')).toBeNull())
  })

  it('같은 이름 후보가 여럿이면 고르게 한다 (FR-303)', async () => {
    renderAt('/ask')
    fireEvent.change(await screen.findByRole('textbox', { name: '질문' }), { target: { value: '친구 촬영지' } })
    fireEvent.click(screen.getByRole('button', { name: '묻기' }))
    fireEvent.click(await screen.findByRole('button', { name: '친구 (2001)' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: '친구 (2001)' })).toBeNull())
  })

  it('범위 밖 질문은 검색하지 않고 서비스 범위를 알린다 (FR-309)', async () => {
    renderAt('/ask')
    fireEvent.change(await screen.findByRole('textbox', { name: '질문' }), { target: { value: '내일 부산 날씨 어때' } })
    fireEvent.click(screen.getByRole('button', { name: '묻기' }))
    expect(await screen.findByText(/촬영지와 작품에 대해서만 답할 수 있어요/)).toBeTruthy()
    expect(screen.queryByRole('heading', { name: /촬영지 \d+곳/ })).toBeNull()
  })

  it('AI가 답하지 못하면 필터 패널을 열고 뽑은 조건을 미리 채운다 (FR-311)', async () => {
    renderAt('/ask')
    fireEvent.change(await screen.findByRole('textbox', { name: '질문' }), { target: { value: 'AI 장애 시험 해운대' } })
    fireEvent.click(screen.getByRole('button', { name: '묻기' }))
    const panel = await screen.findByRole('form', { name: '필터로 찾기' })
    await waitFor(() => expect((within(panel).getByRole('checkbox', { name: '해운대구' }) as HTMLInputElement).checked).toBe(true))
    fireEvent.click(within(panel).getByRole('button', { name: '이 조건으로 찾기' }))
    expect(await screen.findByRole('heading', { name: /촬영지 \d+곳/ })).toBeTruthy()
  })

  it('서버에 AI 탐색 API가 아직 없으면(404) 알아듣는 말로 안내한다', () => {
    expect(queryErrorMessage(new ApiError(404, 'NOT_FOUND', 'Not Found'), () => {})).toMatch(/아직 서버에 준비되지 않았어요/)
  })

  it('전화번호를 넣으면 가렸다고 알린다 (FR-702)', async () => {
    renderAt('/ask')
    fireEvent.change(await screen.findByRole('textbox', { name: '질문' }), { target: { value: '010-0000-0000 해운대 촬영지' } })
    fireEvent.click(screen.getByRole('button', { name: '묻기' }))
    expect(await screen.findByText('개인정보로 보이는 내용을 가렸습니다')).toBeTruthy()
  })
})

// ---------------------------------------------------------------- F4
describe('코스 (UI-06·07, FR-401~406)', () => {
  it('추천 코스마다 장소 수·총 소요 시간·이동 거리를 보여 준다 (FR-401)', async () => {
    renderAt('/courses')
    const card = await screen.findByRole('link', { name: /원도심 영화 골목 걷기/ })
    expect(card.textContent).toMatch(/3곳/)
    expect(card.textContent).toMatch(/약 .*(분|시간)/)
    expect(card.textContent).toMatch(/이동 .*(m|km)/)
  })

  it('테마에 코스가 없으면 나만의 코스 만들기로 안내한다', async () => {
    renderAt('/courses')
    fireEvent.click(await screen.findByRole('button', { name: '배우' }))
    expect(await screen.findByText('배우 테마 추천 코스가 없어요.')).toBeTruthy()
  })

  it('코스 상세는 출발·구간 이동·체류 시간·총 소요 시간을 보여 준다 (FR-402·413)', async () => {
    renderAt('/courses/1')
    expect(await screen.findByRole('heading', { level: 1, name: '원도심 영화 골목 걷기' })).toBeTruthy()
    expect(screen.getByText(/^출발 · /)).toBeTruthy()
    expect(screen.getAllByText(/^머무는 시간 30분$/).length).toBe(3)
    expect(screen.getAllByText(/걸어서 \d+분|도시철도 \d+분/).length).toBeGreaterThan(0)
  })

  it('저장한 추천 코스는 API-010으로 다시 받아 체류 시간을 지킨다', async () => {
    const get = vi.spyOn(api, 'get')
    const post = vi.spyOn(api, 'post')
    const r = savedCourses.save({ title: '원도심 영화 골목 걷기', start: { type: 'location', id: '4' }, start_label: '영도대교', location_ids: [4, 6, 2], fixed_order: true, course_id: 1 })
    renderAt(`/courses/saved/${r.ok ? r.key : ''}`)
    expect(await screen.findByRole('heading', { level: 1, name: '원도심 영화 골목 걷기' })).toBeTruthy()
    expect(screen.getAllByText(/^머무는 시간 30분$/).length).toBe(3)
    expect(get.mock.calls.some(([path]) => path === '/courses/1')).toBe(true)
    expect(post.mock.calls.some(([path]) => path === '/courses/plan')).toBe(false)
    get.mockRestore()
    post.mockRestore()
  })

  it('저장한 추천 코스가 없어졌으면(404) 저장한 순서로 다시 계산한다', async () => {
    const post = vi.spyOn(api, 'post')
    const r = savedCourses.save({ title: '없어진 코스', start: { type: 'location', id: '4' }, start_label: '영도대교', location_ids: [4, 6], fixed_order: true, course_id: 99 })
    renderAt(`/courses/saved/${r.ok ? r.key : ''}`)
    expect(await screen.findByRole('heading', { level: 1, name: '없어진 코스' })).toBeTruthy()
    expect(screen.getAllByText(/^머무는 시간 \d+분$/).length).toBe(2)
    expect(post.mock.calls.some(([path]) => path === '/courses/plan')).toBe(true)
    post.mockRestore()
  })

  it('나만의 코스는 촬영지 2곳부터 만들 수 있다 (FR-403 경계)', async () => {
    renderAt('/courses?tab=my')
    const make = (await screen.findByRole('button', { name: '코스 만들기' })) as HTMLButtonElement
    fireEvent.change(screen.getByRole('combobox', { name: '지역' }), { target: { value: '중구' } })
    const rows = await screen.findAllByRole('checkbox')
    const places = rows.filter((r) => r.closest('.pick-row'))
    fireEvent.click(places[0])
    expect(make.disabled).toBe(true) // 1곳
    expect(screen.getByText('촬영지를 1곳 더 골라 주세요.')).toBeTruthy()
    fireEvent.click(places[1])
    expect(make.disabled).toBe(false) // 2곳
    fireEvent.click(make)
    expect(await screen.findByRole('heading', { name: '나만의 코스' })).toBeTruthy()
    expect(screen.getAllByText(/^머무는 시간 \d+분$/).length).toBe(2)
  })

  it('순서를 직접 바꾸면 그 순서로 다시 계산한다 (FR-405)', async () => {
    renderAt('/courses?tab=my')
    fireEvent.change(await screen.findByRole('combobox', { name: '지역' }), { target: { value: '중구' } })
    const places = (await screen.findAllByRole('checkbox')).filter((r) => r.closest('.pick-row'))
    fireEvent.click(places[0])
    fireEvent.click(places[1])
    fireEvent.click(screen.getByRole('button', { name: '코스 만들기' }))
    await screen.findByRole('heading', { name: '나만의 코스' })
    const firstBefore = document.querySelector('.tl-stop strong')?.textContent
    fireEvent.click(screen.getAllByRole('button', { name: /뒤로$/ })[0])
    await waitFor(() => expect(screen.getByText('내가 정한 순서')).toBeTruthy())
    expect(document.querySelector('.tl-stop strong')?.textContent).not.toBe(firstBefore)
  })

  it('코스는 10개까지 저장하고, 11번째는 막는다 (FR-406 경계)', async () => {
    for (let i = 0; i < MAX_SAVED_COURSES - 1; i++)
      savedCourses.save({ title: `코스${i}`, start: { type: 'current' }, start_label: '현재 위치', location_ids: [4, 6], fixed_order: false })
    renderAt('/courses/1')
    fireEvent.click(await screen.findByRole('button', { name: '이 코스 저장' }))
    expect(await screen.findByText(/이 브라우저에 저장했어요/)).toBeTruthy() // 10번째
    expect(savedCourses.all().length).toBe(MAX_SAVED_COURSES)
    fireEvent.click(screen.getByRole('button', { name: '이 코스 저장' }))
    expect(await screen.findByText(/10개까지 저장할 수 있어요/)).toBeTruthy() // 11번째
    expect(savedCourses.all().length).toBe(MAX_SAVED_COURSES)
  })

  it('현재 위치 출발 코스는 좌표 없이 저장한다 (BR-10)', () => {
    const r = savedCourses.save({ title: 't', start: { type: 'current' }, start_label: '현재 위치', location_ids: [4, 6], fixed_order: false })
    expect(r.ok).toBe(true)
    expect(JSON.stringify(savedCourses.all()[0].start)).toBe('{"type":"current"}')
  })

  it('주변 시설은 구간마다 고르고, 0건 구간은 따로 안내한다 (FR-414~418)', async () => {
    renderAt('/courses/1')
    fireEvent.click(await screen.findByRole('button', { name: '주변 시설 찾기' }))
    expect(await screen.findAllByRole('link', { name: '카카오맵 길찾기' })).toBeTruthy()
    expect(screen.getAllByRole('heading', { level: 3 }).length).toBeGreaterThanOrEqual(2)
  })
})

// ---------------------------------------------------------------- F5
describe('방문 인증 (UI-02, FR-501~506) 경계', () => {
  it('정확도 50m는 인증하고 50.1m는 실외 안내를 한다 (FR-502·503)', async () => {
    setPosition(BRIDGE.lat, BRIDGE.lon, 50.1)
    renderAt(`/locations/${BRIDGE.id}`)
    fireEvent.click(await screen.findByRole('button', { name: '방문 인증' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/실외/)
    expect(tokens.all().length).toBe(0)

    setPosition(BRIDGE.lat, BRIDGE.lon, 50)
    fireEvent.click(screen.getByRole('button', { name: '방문 인증' }))
    expect(await screen.findByText('인증 완료')).toBeTruthy()
    expect(tokens.all().length).toBe(1)
  })

  it('반경 안(99m)은 인증하고 반경 밖(101m)은 남은 거리를 알려 준다 (FR-502·503)', async () => {
    setPosition(BRIDGE.lat + 101 / M_PER_DEG_LAT, BRIDGE.lon, 10)
    renderAt(`/locations/${BRIDGE.id}`)
    fireEvent.click(await screen.findByRole('button', { name: '방문 인증' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/약 101m 남았어요/)

    setPosition(BRIDGE.lat + 99 / M_PER_DEG_LAT, BRIDGE.lon, 10)
    fireEvent.click(screen.getByRole('button', { name: '방문 인증' }))
    expect(await screen.findByText('인증 완료')).toBeTruthy()
  })

  it('이미 인증한 장소는 인증 완료로 보여 준다 (FR-505)', async () => {
    await stampDirect(BRIDGE.id, BRIDGE.lat, BRIDGE.lon)
    renderAt(`/locations/${BRIDGE.id}`)
    expect(await screen.findByText('인증 완료')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '방문 인증' })).toBeNull()
  })

  it('서버가 이미 인증했다고 하면 그렇게 안내한다 (FR-505)', () => {
    expect(stampMessage({ ok: false, token: null, reason: 'already_certified', distance_m: null }, 100, 10).text).toMatch(/이미 인증한 장소/)
  })

  it('인증 제외 장소는 버튼을 막고 이유를 보여 준다 (FR-506)', async () => {
    renderAt('/locations/7') // 주거지
    const btn = (await screen.findByRole('button', { name: '방문 인증' })) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(screen.getByText('주거지라 방문 인증 대상이 아니에요.')).toBeTruthy()
  })

  it('위치 권한이 없으면 이유를 알려 준다', async () => {
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: { getCurrentPosition: (_ok: PositionCallback, err: PositionErrorCallback) => err({ code: 1, PERMISSION_DENIED: 1 } as GeolocationPositionError) },
    })
    renderAt(`/locations/${BRIDGE.id}`)
    fireEvent.click(await screen.findByRole('button', { name: '방문 인증' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/위치 권한/)
  })
})

describe('스탬프북·완주 배지 (UI-08, FR-507~509)', () => {
  it('저장 위치 안내를 항상 보여 준다 (FR-509)', async () => {
    renderAt('/me')
    expect(await screen.findByText(/이 브라우저에만 저장되며/)).toBeTruthy()
    expect(screen.getByText('아직 받은 스탬프가 없어요.')).toBeTruthy()
  })

  it('촬영지명·작품·인증 일시를 보여 주고, 서명이 맞지 않는 토큰은 보이지 않는다 (FR-507)', async () => {
    await stampDirect(BRIDGE.id, BRIDGE.lat, BRIDGE.lon)
    tokens.add('위조된-토큰')
    renderAt('/me')
    expect(await screen.findByRole('link', { name: '영도대교' })).toBeTruthy()
    expect(screen.getByText(/인증$/)).toBeTruthy()
    expect(screen.getByText('스탬프 1개')).toBeTruthy()
    expect(screen.getByText('확인되지 않은 기록 1개는 보여 주지 않아요.')).toBeTruthy()
  })

  it('인증 대상을 다 모으기 전에는 배지 버튼이 막혀 있고, 다 모으면 배지를 받는다 (FR-508)', async () => {
    // 추천 코스 1: 영도대교(4)·충무동 새벽시장(6)·비석문화마을(2)
    await stampDirect(4, 35.0948, 129.0368)
    await stampDirect(6, 35.094, 129.023)
    renderAt('/courses/1')
    expect(await screen.findByText('방문 인증 2/3')).toBeTruthy()
    expect((screen.getByRole('button', { name: '완주 배지 받기' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('세 곳을 모두 인증하면 완주 배지를 받고 스탬프북에 보인다 (FR-508)', async () => {
    await stampDirect(4, 35.0948, 129.0368)
    await stampDirect(6, 35.094, 129.023)
    await stampDirect(2, 35.099, 129.0145)
    const view = renderAt('/courses/1')
    await screen.findByText('방문 인증 3/3')
    const btn = screen.getByRole('button', { name: '완주 배지 받기' }) as HTMLButtonElement
    await waitFor(() => expect(btn.disabled).toBe(false))
    fireEvent.click(btn)
    expect(await screen.findByText(/완주 배지를 받았어요/)).toBeTruthy()
    view.unmount()
    renderAt('/me')
    expect(await screen.findByText('완주 배지 1개')).toBeTruthy()
    expect(screen.getByText('원도심 영화 골목 걷기')).toBeTruthy()
  })

  it('인증 대상이 1곳뿐인 코스는 배지를 주지 않는다 (BR-09 경계)', async () => {
    const r = await mockRequest({ method: 'POST', path: '/badges', body: { course: { location_ids: [4, 7] }, stamps: [] }, headers: {} })
    expect((r.data as { reason: string }).reason).toBe('too_few_targets')
  })

  it('저장한 코스를 지울 때 한 번 더 묻는다', async () => {
    savedCourses.save({ title: '지울 코스', start: { type: 'station', id: '113' }, start_label: '부산역역(1호선)', location_ids: [4, 6], fixed_order: false })
    renderAt('/me?tab=courses')
    fireEvent.click(await screen.findByRole('button', { name: '삭제' }))
    fireEvent.click(screen.getByRole('button', { name: '지우기' }))
    expect(await screen.findByText('저장한 코스가 없어요.')).toBeTruthy()
  })
})
