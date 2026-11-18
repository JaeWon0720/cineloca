// F1 작품 아카이브 API 타입 (API-001~002) — 초안.
// 백엔드 F1 스키마(R4 담당)가 올라오면 그 형식에 맞춰 고친다. 이름은 F2 스키마(WorkBrief, kmdb_matched)를 따랐다.
import type { SourceCard } from './types.ts'

export type WorkSummary = {
  id: number
  title: string
  release_year: number | null
  kind: string // 영화·드라마
  genres: string[]
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
  scene: string | null
}

/** API-002 응답. KMDb 미수록 작품(kmdb_matched=false)은 작품명과 촬영지만 쓴다(UC-01). */
export type WorkDetail = WorkSummary & {
  cast: string[] // 주요 출연진, 화면에는 최대 5명(FR-103)
  synopsis: string | null // KMDb 원문 줄거리. 출처는 sources에 함께 온다
  rating: string | null // 관람등급(FR-107)
  poster_url: string | null // KMDb 이미지 주소. 파일은 저장하지 않는다(FR-104, BR-01)
  still_urls: string[] // KMDb 스틸 이미지 주소(FR-104)
  trailer_url: string | null // KMDb 예고편(VOD) 링크, 없으면 null(FR-105)
  kmdb_url: string | null // KMDb 작품 페이지
  locations: WorkLocation[]
  sources: SourceCard[]
}
