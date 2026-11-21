// 관리자 API 타입: API-020 관리자 로그인 (FR-615, SDD 3.2).
// 백엔드 app/api/schemas/admin.py와 같게 둔다. 세션 토큰은 HttpOnly 쿠키로만 오가고 화면 코드는 만지지 않는다.

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
