// F5 방문 인증 API 타입 (API-016~018). 백엔드 app/api/schemas/stamps.py와 같은 모양이다.
// 서버는 위치·스탬프를 저장하지 않는다. 토큰은 이 브라우저에만 보관하고 필요할 때 다시 보낸다(ADR-009, FR-504).
import type { WorkBrief } from './locations.ts'

export const MAX_ACCURACY_M = 50 // FR-502: 위치 정확도 50m 이하일 때만 인증

export type StampRequest = { location_id: number; lat: number; lon: number; accuracy_m: number; held: string[] }
export type StampResponse = {
  ok: boolean
  token: string | null
  reason: 'low_accuracy' | 'out_of_range' | 'already_certified' | null
  distance_m: number | null // 반경 밖일 때만. 정확도 50m 초과면 알려 주지 않음(정정표 11/18)
}

export type StampEntry = {
  location: { id: number; name: string }
  works: WorkBrief[] // 최근 작품 3편
  work_count: number
  certified_at: string
}
export type BadgeEntry = { course_key: string; course_title: string | null; issued_at: string }
export type VerifyResponse = { valid: StampEntry[]; badges: BadgeEntry[]; invalid_count: number; unavailable_count: number }

/** 추천 코스는 id, 저장한 코스는 촬영지 목록으로 가리킨다 */
export type CourseRef = { id?: number | null; location_ids?: number[] | null }
export type BadgeRequest = { course: CourseRef; stamps: string[] }
export type BadgeResponse = {
  ok: boolean
  token: string | null
  reason: 'missing_stamps' | 'too_few_targets' | null // 완주 배지는 인증 대상 2곳 이상 코스만(BR-09)
  course_key: string
  missing: number[]
}
