// F2 지도 기반 탐색 API 타입 (API-005~007).
// 백엔드 app/api/schemas/locations.py, meta.py와 같은 모양이다. 형식이 바뀌면 여기와 목업을 함께 고친다.
import type { SourceCard } from './types.ts'

export type RadiusM = 500 | 1000 | 3000 // FR-203
export type Caution = 'private' | 'residential' | 'school' // FR-209
export type CertExcludedReason = 'no_coordinates' | 'closed' | 'private' | 'residential' | 'school' | 'excluded'

export type BasePoint = { lat: number; lon: number }

/** API-005 요청 */
export type LocationSearchRequest = {
  base: BasePoint
  radius_m?: RadiusM
  genres?: string[] // 장르 묶음 이름(API-007 meta.genres, KMDb 장르 38종을 12개로 묶음)
  year_from?: number | null
  year_to?: number | null
  gu?: string[]
}

export type StationBrief = { id: string; name: string; line: string; walk_min: number }
export type WorkBrief = { id: number; title: string; release_year: number | null }

/** 지도 마커·거리순 목록·요약 카드 한 건 */
export type LocationHit = {
  id: number
  name: string
  gu: string | null
  lat: number
  lon: number
  distance_m: number
  works: WorkBrief[]
  work_count: number
  nearest_station: StationBrief | null
  directions_url: string
}

/** API-005 응답. items는 거리순이고 좌표 없는 촬영지는 빠진다(FR-210). */
export type LocationSearchResponse = { radius_m: RadiusM; total: number; items: LocationHit[]; sources: SourceCard[] }

export type Photo = { url: string; title: string | null; source_id: string }
export type CertInfo = { eligible: boolean; radius_m: number; excluded_reason: CertExcludedReason | null }
export type LocationWork = WorkBrief & { kmdb_matched: boolean }

/** API-006 응답. 좌표가 없으면 located=false이고 lat·lon·directions_url은 null. */
export type LocationDetail = {
  id: number
  name: string
  address: string | null
  gu: string | null
  located: boolean
  lat: number | null
  lon: number | null
  tags: string[]
  photos: Photo[]
  nearest_station: StationBrief | null
  caution: Caution | null
  caution_notice: string | null
  safety_note: string | null
  cert: CertInfo
  works: LocationWork[]
  directions_url: string | null
  sources: SourceCard[]
}

export type NamedPoint = { label: string; lat: number; lon: number }
export type DistrictOption = { name: string; lat: number; lon: number }
export type StationOption = { id: string; name: string; line: string; lat: number; lon: number }

/** API-007 선택 목록 */
export type MetaResponse = {
  default_base: NamedPoint
  radius_options: RadiusM[]
  default_radius_m: RadiusM
  districts: DistrictOption[]
  stations: StationOption[]
  genres: string[] // 장르 묶음 12개
  year_range: { min: number; max: number } | null
}
