// 관리자 API 타입: API-020 관리자 로그인 (FR-615, SDD 3.2).
// 백엔드 app/api/schemas/admin.py와 같게 둔다. 세션 토큰은 HttpOnly 쿠키로만 오가고 화면 코드는 만지지 않는다.
import type { Caution } from './locations.ts'

/** GET·POST /admin/session 응답 */
export type AdminSession = { authenticated: boolean; expires_at: string }

/** 관리자 메뉴: 화면 번호와 주소. 새 관리 화면은 여기와 App.tsx 라우트에 함께 붙인다. */
export const ADMIN_MENU = [
  { to: '/admin/sync', label: '동기화 리포트', screen: 'UI-10' },
  { to: '/admin/review', label: '검수 큐·속성 편집', screen: 'UI-11' },
  { to: '/admin/courses', label: '추천 코스 관리', screen: 'UI-12' },
] as const

/** 관리자 비밀번호 최대 길이(백엔드 MAX_PASSWORD_CHARS와 같게) */
export const ADMIN_PASSWORD_MAX = 200

// ── API-021 동기화 요청·리포트 (FR-612·613·616, NFR-005) — backend app/api/schemas/admin.py·app/batch/report.py와 같게
export type SyncTrigger = 'schedule' | 'manual'
export type SyncStatus = 'requested' | 'running' | 'succeeded' | 'failed' | 'guarded'

/** POST /admin/sync 응답: created=false면 이미 대기·실행 중인 동기화의 run_id */
export type SyncRequestResult = { run_id: number; created: boolean; message: string }

export type SyncCounts = {
  fetched: Record<string, number>
  works: number
  locations: number
  match: { matched: number; review: number; unmatched: number }
  geo: { located: number; failed: number; by_source: Record<string, number> }
  unknown_genres: string[]
}

export type SyncReport = {
  run_id: number
  trigger: SyncTrigger
  status: SyncStatus
  started_at: string | null
  finished_at: string | null
  counts: SyncCounts | null
  rates: { match_rate: number | null; review_rate: number | null; geo_rate: number | null } | null
  prev_counts: SyncCounts | null
  /** 건수 가드 위반 사유(FR-612). 비어 있으면 통과 */
  guard: string[]
  warnings: string[]
  failures: { dataset: string | null; step: string; reason: string }[]
}

/** GET /admin/sync 응답: UI-10 한 화면에 필요한 값 */
export type SyncOverview = {
  active: { run_id: number; trigger: SyncTrigger; status: 'requested' | 'running'; requested_at: string; started_at: string | null } | null
  alerts: string[]
  /** 최근 10회, 최신 먼저 */
  reports: SyncReport[]
  usage_day: string
  usage: { key: string; count: number; limit: number | null; ratio: number | null; level: 'ok' | 'warning' | 'exhausted' }[]
  schedule: { interval_days: number; last_started_at: string | null; next_due_at: string | null }
}

export const SYNC_STATUS_LABEL: Record<SyncStatus, string> = {
  requested: '대기 중',
  running: '실행 중',
  succeeded: '성공',
  failed: '실패',
  guarded: '가드로 중단',
}
export const SYNC_TRIGGER_LABEL: Record<SyncTrigger, string> = { schedule: '정기', manual: '수동' }

// ── API-022 검수 큐 조회·처리 (FR-608·609, BR-04) / API-023 촬영지 속성 편집 (FR-610, BR-06·07)
// R4 #110이 만들 응답 형식으로 '제안'한 것이다. DB review_item·location 열 이름을 그대로 쓴다.
// 백엔드가 다르게 정하면 이 파일과 목업(mocks/handlers/admin.ts)만 맞추면 된다.
export type ReviewKind = 'tag' | 'match'
export type ReviewStatus = 'pending' | 'approved' | 'modified' | 'rejected'

/** 검수 항목 proposal(JSONB). backend app/batch/ai_review.py·app/data/staging.py 형식 */
export type TagProposal = { tags: string[]; reason?: string | null; error?: string }
export type MatchProposal = {
  kmdb_id: string | null
  /** 규칙 항목(C1 매칭) */
  kmdb_title?: string
  prod_year?: string | null
  rel_date?: string | null
  /** AI 항목(FR-608) */
  verdict?: 'match' | 'not_listed' | 'unsure'
  title?: string
  year?: number | null
  reason?: string | null
}

export type ReviewItem = {
  id: number
  kind: ReviewKind
  target_type: 'location' | 'work'
  target_id: number
  /** 작품 제목 또는 촬영지 이름 */
  target_name: string
  /** 작품이면 개봉연도, 촬영지면 null */
  target_year: number | null
  proposer: 'rule' | 'ai'
  score: number | null
  status: ReviewStatus
  proposal: TagProposal | MatchProposal
  created_at: string
  handled_at: string | null
}

/** GET /admin/reviews?status=pending|handled&kind=tag|match&page=1&size=20 */
export type ReviewListResponse = {
  total: number
  page: number
  size: number
  counts: { pending: number; handled: number }
  items: ReviewItem[]
}

/** PATCH /admin/reviews/{id}: modify면 tags(태그) 또는 kmdb_id(매칭)로 고친 값을 함께 보낸다 */
export type ReviewAction = { action: 'approve' | 'modify' | 'reject'; tags?: string[]; kmdb_id?: string | null }

/** GET·PATCH /admin/locations/{id} */
export type AdminLocationAttrs = {
  id: number
  name: string
  gu: string | null
  address: string | null
  located: boolean
  tags: string[]
  caution: Caution | null
  safety_note: string | null
  cert_radius_m: number
  cert_excluded: boolean
}
export type AdminLocationPatch = Partial<Pick<AdminLocationAttrs, 'tags' | 'caution' | 'safety_note' | 'cert_radius_m' | 'cert_excluded'>>

/** BR-07 인증 반경 범위, 안전 안내 길이 */
export const CERT_RADIUS_MIN = 50
export const CERT_RADIUS_MAX = 300
export const SAFETY_NOTE_MAX = 200

export const CAUTION_LABEL: Record<Caution, string> = { private: '사유지', residential: '주거지', school: '학교' }
export const REVIEW_STATUS_LABEL: Record<ReviewStatus, string> = { pending: '대기', approved: '승인', modified: '수정 승인', rejected: '반려' }

/** 장소 유형 태그 20종 (backend app/core/place_tags.py PlaceType과 같게) */
export const PLACE_TAGS = [
  '해변',
  '항구',
  '시장',
  '골목·주택가',
  '거리·도로',
  '다리',
  '역·교통',
  '공원·산',
  '학교',
  '병원',
  '관공서',
  '아파트',
  '숙박',
  '식당·카페',
  '상점',
  '문화·관광',
  '종교·역사',
  '체육',
  '산업시설',
  '업무 건물',
] as const
