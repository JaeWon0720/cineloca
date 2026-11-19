// AI 요청 오류 처리: 이용 안내 미확인(403 NOTICE_REQUIRED)이면 안내 창을 다시 띄운다(FR-701)
import { ApiError } from '../api/client.ts'

/** 이용 안내를 확인하지 않아 거절(403 NOTICE_REQUIRED)되면 안내 창을 다시 띄우고 안내 문장을 돌려준다. */
export function aiErrorMessage(e: unknown, showNotice: () => void): string {
  if (e instanceof ApiError && e.code === 'NOTICE_REQUIRED') {
    showNotice()
    return '이용 안내를 확인한 뒤 다시 눌러 주세요.'
  }
  return e instanceof Error ? e.message : '요청을 처리하지 못했습니다.'
}

/** AI 탐색 API가 아직 서버에 없을 때(404) 사람이 알아듣는 안내로 바꾼다. */
export function queryErrorMessage(e: unknown, showNotice: () => void): string {
  if (e instanceof ApiError && e.status === 404 && e.code === 'NOT_FOUND')
    return 'AI 탐색 기능이 아직 서버에 준비되지 않았어요. 지도나 작품 탭에서 찾아 주세요.'
  return aiErrorMessage(e, showNotice)
}
