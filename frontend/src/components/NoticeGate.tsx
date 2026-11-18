// FR-701 첫 방문 이용 안내. 확인한 버전은 이 브라우저에만 저장한다.
import { useState, type ReactNode } from 'react'
import { NOTICE_VERSION } from '../api/notice.ts'
import { NoticeContext } from '../lib/notice.ts'
import { store } from '../lib/storage.ts'

export function NoticeProvider({ children }: { children: ReactNode }) {
  const [accepted, setAccepted] = useState(store.get<string>('notice') === NOTICE_VERSION)
  const [open, setOpen] = useState(!accepted)

  function accept() {
    store.set('notice', NOTICE_VERSION)
    setAccepted(true)
    setOpen(false)
  }

  return (
    <NoticeContext value={{ accepted, show: () => setOpen(true) }}>
      {children}
      {open && <NoticeDialog onAccept={accept} />}
    </NoticeContext>
  )
}

function NoticeDialog({ onAccept }: { onAccept: () => void }) {
  return (
    <div className="modal-back" role="dialog" aria-modal="true" aria-labelledby="notice-title">
      <div className="modal">
        <h2 id="notice-title">이용 안내</h2>
        <ul>
          <li>
            AI 탐색·AI 코스·AI 작품 소개에 입력한 내용은 외부 AI(무료 서비스 포함)로 전송될 수 있습니다. 이름·전화번호 같은
            개인정보를 넣지 마세요. 개인정보로 보이는 내용은 자동으로 가려집니다.
          </li>
          <li>
            AI가 만든 문장에는 ‘AI 생성’ 표시가 붙고, 시스템이 찾은 근거로만 만들어집니다. 그래도 틀릴 수 있으니 출처를 함께
            확인하세요.
          </li>
          <li>위치는 주변 검색과 방문 인증에만 쓰고 저장하지 않습니다. 스탬프와 저장한 코스는 이 브라우저에만 남습니다.</li>
          <li>촬영지 중에는 주거지·학교·영업장이 있습니다. 주민과 이용객을 배려해 주세요.</li>
        </ul>
        <button className="btn primary" onClick={onAccept}>
          확인했어요
        </button>
      </div>
    </div>
  )
}
