// F4 추천·나만의 코스 + 주변 시설 추천 API 타입 (API-009~012).
// 백엔드 app/api/schemas/courses.py·amenities.py와 같은 모양이다.
import type { CertInfo } from './locations.ts'
import type { SourceCard } from './types.ts'

export type Theme = 'work' | 'person' | 'district'
export const THEME_LABEL: Record<Theme, string> = { work: '작품', person: '배우', district: '지역' }

export type StartType = 'current' | 'station' | 'location'
/** 출발지: 현재 위치(lat·lon), 도시철도역(id=역 코드), 촬영지(id=촬영지 번호) */
export type StartIn = { type: StartType; lat?: number | null; lon?: number | null; id?: string | null }
export type StartOut = { type: StartType; label: string; lat: number | null; lon: number | null }

export type CourseStop = {
  order: number
  id: number
  name: string
  gu: string | null
  lat: number
  lon: number
  stay_min: number // 체류 시간(BR-13)
  cert: CertInfo
}

export type StationRef = { id: string; name: string; line: string }

/** 구간 하나. from_order 0은 출발지. 도시철도면 탈 역·내릴 역과 걷는 시간이 함께 온다(FR-413, BR-12). */
export type CourseLeg = {
  from_order: number
  to_order: number
  mode: 'walk' | 'subway'
  minutes: number
  distance_m: number
  board?: StationRef | null
  alight?: StationRef | null
  walk_to_board_min?: number
  ride_min?: number
  walk_from_alight_min?: number
}

/** API-011 응답(나만의 코스)과 API-010 응답(추천 코스 상세)의 공통 부분 */
export type CoursePlan = {
  start: StartOut
  stops: CourseStop[]
  legs: CourseLeg[]
  travel_min: number
  stay_min: number
  total_min: number
  total_distance_m: number
  optimized: boolean // false면 받은 순서 그대로(FR-405 순서 직접 변경)
  subway_data: boolean // false면 역간 소요시간이 없어 모두 도보로 계산
}

export type PlanRequest = { start: StartIn; location_ids: number[]; fixed_order?: boolean }

export type CourseSummary = {
  id: number
  title: string
  theme: Theme
  theme_ref: string | null
  stop_count: number
  total_min: number
  total_distance_m: number
}
export type CourseList = { items: CourseSummary[] }
export type CourseDetail = CoursePlan & { id: number; title: string; theme: Theme; theme_ref: string | null }

/** 나만의 코스: 촬영지 2~10곳(FR-403, 출발 촬영지 포함) */
export const MIN_STOPS = 2
export const MAX_STOPS = 10

// ---------- API-012 코스 주변 시설 추천 (FR-414~418)
export type FacilityKind = 'food' | 'lodging'
export const FOOD = ['밥', '카페'] as const
export const LODGING = ['호텔', '콘도·레지던스', '펜션·민박', '모텔', '캠핑', '게스트하우스·호스텔'] as const
export type FoodCategory = (typeof FOOD)[number]
export type LodgingCategory = (typeof LODGING)[number]

export type SelectedConditions = { kinds?: FacilityKind[]; food?: FoodCategory[]; lodging?: LodgingCategory[] }
export type AmenityRequest = { location_ids: number[]; text?: string | null; selected?: SelectedConditions | null }
export const AMENITY_TEXT_MAX = 200

export type AmenityPick = {
  content_id: string
  name: string
  category: string | null
  address: string | null
  lat: number
  lon: number
  distance_m: number
  matched: string[]
  reason: string
  ai_generated: boolean // true면 'AI 생성' 표시(FR-707)
  directions_url: string
}
export type AmenitySegment = {
  key: string
  kind: FacilityKind
  from_order: number
  to_order: number | null
  anchor: string
  notice: string | null // 조회 실패('시설 정보를 불러오지 못함')와 0건('근처 시설 없음')을 구분한 안내
  picks: AmenityPick[]
}
export type AmenityResponse = {
  conditions: {
    status: 'ai' | 'selected' | 'manual'
    kinds: FacilityKind[]
    food: FoodCategory[]
    lodging: LodgingCategory[]
    keywords: string[]
    masked: boolean
  }
  notices: string[]
  segments: AmenitySegment[]
  sources: SourceCard[]
}
