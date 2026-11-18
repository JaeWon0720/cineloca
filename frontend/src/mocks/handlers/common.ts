// 공통 API 목업: 상태 확인(API-025), 데이터 출처(UI-09)
import type { SourcesInfo } from '../../api/types.ts'
import raw from '../data/common.json'
import { ok, type Handler } from '../http.ts'

const DATA = raw as unknown as { sources: SourcesInfo }

export const commonHandlers: Handler[] = [
  { method: 'GET', path: /^\/health$/, handle: () => ok({ status: 'ok', version: 'mock' }) },
  { method: 'GET', path: /^\/sources$/, handle: () => ok(DATA.sources) },
]
