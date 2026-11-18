// 이용 안내(FR-701) 상태. 안내를 확인하기 전에는 AI 요청이 거절(403)되므로,
// 화면은 NOTICE_REQUIRED 오류를 받으면 show()로 안내 창을 다시 띄운다.
import { createContext, useContext } from 'react'

export type NoticeState = { accepted: boolean; show: () => void }

export const NoticeContext = createContext<NoticeState>({ accepted: false, show: () => {} })

export const useNotice = () => useContext(NoticeContext)
