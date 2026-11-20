// F1 작품 아카이브 API 타입 (API-001~004). 백엔드 app/api/schemas/works.py·work_intro.py와 같은 모양이다.
// 포스터·스틸은 저장도 표시도 하지 않는다(구현 기준 정정표 11/18, #58). 작품 API도 poster_url·still_urls를 주지 않는다.
import type { SourceCard } from './types.ts'

export type WorkBrief = { id: number; title: string; release_year: number | null }

export type WorkSummary = WorkBrief & {
  genres: string[] // KMDb 장르 원문
  director: string | null
  location_count: number
  kmdb_matched: boolean // false면 '촬영지 정보 제공 작품'(FR-604)
}

export const WORKS_PAGE_SIZE = 20 // FR-101: 한 쪽에 20편

/** API-001 응답. page는 1부터 센다. */
export type WorkListResponse = { total: number; page: number; size: number; items: WorkSummary[]; sources: SourceCard[] }

export type WorkLocation = {
  id: number
  name: string
  gu: string | null
  located: boolean
  lat: number | null
  lon: number | null
}

/** API-002 응답. KMDb 미연결 작품(kmdb_matched=false)은 작품명과 촬영지만 쓴다(UC-01). */
export type WorkDetail = WorkSummary & {
  cast: string[] // 주요 출연진, 화면에는 최대 5명(FR-103)
  synopsis: string | null // KMDb 줄거리(공공누리 제1유형)
  trailer_url: string | null // 예고편 링크, 없으면 null(FR-105)
  kmdb_url: string | null // KMDb 원문 페이지
  locations: WorkLocation[]
  related: WorkBrief[]
  sources: SourceCard[]
}

/** API-003 응답: 배치가 저장한 KMDb 관람등급. 없으면 null(FR-107). */
export type WorkRating = { rating: string | null; sources: SourceCard[] }

/** API-004 AI 작품 소개 (FR-108~111) */
export type SpoilerLevel = 'none' | 'partial'
export type WorkIntroResponse = {
  status: 'ok' | 'degraded' | 'not_eligible'
  spoiler: SpoilerLevel
  sentences: string[]
  ai_generated: boolean
  fallback: { synopsis: string | null; locations: { name: string; gu: string | null }[] } | null
  notice: string | null
  reason: string | null
  dropped_count: number // 근거 대조에서 지운 문장 수(FR-110)
}

/** KMDb 텍스트를 보여 줄 때 붙이는 출처 표기(정정표 11/18: TBD-01 닫힘, #12·#58) */
export const KMDB_CREDIT = 'KMDb(공공누리 제1유형)'
