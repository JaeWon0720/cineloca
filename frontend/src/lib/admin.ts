// 관리자 화면 공통: 로그인 상태를 관리 화면(UI-10~12)에 나눠 준다.
// 관리 화면은 API를 부를 때 guard()로 감싸면, 세션이 끝났을 때(401 ADMIN_REQUIRED) 자동으로 로그인 화면으로 돌아간다.
import { createContext, useContext } from 'react'
import { ApiError } from '../api/client.ts'

export type AdminContextValue = {
  /** 세션 만료 시각(ISO) */
  expiresAt: string
  logout: () => Promise<void>
  /** 관리 API 호출을 감싼다. 세션이 끝났으면 로그인 화면으로 보내고 오류를 그대로 던진다. */
  guard: <T>(p: Promise<T>) => Promise<T>
}

export const AdminContext = createContext<AdminContextValue | null>(null)

/** 관리 화면(AdminLayout 안)에서 로그인 상태를 쓴다 */
export function useAdmin(): AdminContextValue {
  const v = useContext(AdminContext)
  if (!v) throw new Error('useAdmin은 AdminLayout 안에서만 쓸 수 있어요')
  return v
}

/** 관리 API가 '로그인 필요'로 거절했는지 */
export function isAdminRequired(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401 && e.code === 'ADMIN_REQUIRED'
}

/** 세션 만료 시각을 '오후 3:20' 꼴로 */
export function fmtClock(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' })
}
