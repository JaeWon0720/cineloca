// F3 AI 대화형 탐색 API 타입 (API-013~015, FR-301~312).
// 백엔드 app/api/schemas/query.py(#105)와 같게 둔다.
import type { BasePoint } from './locations.ts'
import type { SourceCard } from './types.ts'

export const QUERY_MAX = 200 // FR-301: 200자 이내

export type QueryStatus = 'ok' | 'not_found' | 'choose' | 'out_of_scope' | 'ai_unavailable'
export type ChipKey = 'works' | 'persons' | 'genres' | 'districts' | 'years' | 'anchor' | 'travel'
export type Ref = { id: string; name: string }

/** 확정된 검색 조건. 필터 패널이 보내고, AI가 실패하면 미리 채울 값으로 받는다(FR-310·311). */
export type Filters = {
  works: Ref[]
  persons: Ref[]
  genres: string[]
  districts: string[]
  year_from: number | null
  year_to: number | null
  anchor: { type: 'station' | 'place' | 'current'; ref: Ref | null } | null
  travel: { mode: 'walk' | 'subway'; max_min: number | null } | null
  result_type: 'location' | 'work'
}
export const EMPTY_FILTERS: Filters = {
  works: [],
  persons: [],
  genres: [],
  districts: [],
  year_from: null,
  year_to: null,
  anchor: null,
  travel: null,
  result_type: 'location',
}

export type Hit = {
  location_id: number
  name: string
  gu: string | null
  lat: number
  lon: number
  works: { title: string; year: number | null }[]
  station: string | null
  walk_min: number | null
  source_ids: string[]
}

/** API-014 응답 */
export type SearchResponse = {
  status: 'ok' | 'not_found'
  notices: string[]
  chips: { key: ChipKey; label: string }[]
  relaxed: string[] // 결과가 없어 푼 조건(FR-305)
  total: number
  hits: Hit[] // 20건까지(FR-306)
  filter_panel: Filters | null
  /** hits에 쓰인 출처 카드. 결과마다 source_ids로 골라 붙인다(FR-704) */
  sources: SourceCard[]
}

/** API-013 응답 */
export type QueryResponse = Omit<SearchResponse, 'status'> & {
  status: QueryStatus
  session_id: string
  masked: boolean // 개인정보로 보이는 내용을 가렸는지(FR-702)
  unknown: string[] // 사전에 없어 뺀 이름(FR-303)
  summary: string[] // 결과 요약 최대 3문장(FR-307)
  ai_generated: boolean
  choices: { key: 'works' | 'persons' | 'anchor'; name: string; options: { id: string; label: string }[] }[]
}

export type QueryRequest = { text: string; base?: BasePoint | null }
