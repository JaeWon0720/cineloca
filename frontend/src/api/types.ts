// 여러 화면이 함께 쓰는 API 응답 타입. 백엔드 app/api/schemas/common.py와 같은 모양이다.

/** 출처 카드 (FR-704, BR-02) */
export type SourceCard = { id: string; provider: string; name: string; base_date: string | null; url: string }

/** 오류 응답: {"error": {"code", "message", "retry_after"}} (SDD 3.2) */
export type ErrorBody = { code: string; message: string; retry_after?: number | null }

/** UI-09 데이터 출처 화면 응답(API-019). 백엔드 app/api/schemas/sources.py SourcesResponse와 같게 둔다. */
export type SourcesInfo = {
  ledger: (SourceCard & { license: string; policy: 'STORE' | 'ID_ONLY' | 'NO_STORE' })[]
  notice_version: string
  licenses: { name: string; license: string }[]
}
