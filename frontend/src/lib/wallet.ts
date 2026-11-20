// 이 브라우저에만 두는 기록(ADR-009): 스탬프·배지 토큰(FR-504·508)과 저장한 코스(FR-406).
// 서버는 위치·스탬프·코스를 저장하지 않는다. 시크릿 창이나 저장소 차단에서도 앱이 멈추지 않게 store를 거친다.
import type { StartIn } from '../api/courses.ts'
import { store } from './storage.ts'

const TOKENS = 'tokens'
const COURSES = 'courses'
export const MAX_SAVED_COURSES = 10 // FR-406: 브라우저에 최대 10개

/** 스탬프·배지 토큰. 서명은 서버가 확인하므로(API-017) 여기서는 문자열만 모아 둔다. */
export const tokens = {
  all(): string[] {
    const v = store.get<unknown>(TOKENS, [])
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  },
  add(token: string): boolean {
    const now = tokens.all()
    if (now.includes(token)) return true
    return store.set(TOKENS, [...now, token])
  },
  /** 서명이 맞지 않는 토큰을 지울 때(스탬프북은 유효한 것만 보여 준다, FR-507) */
  replace(list: string[]): boolean {
    return store.set(TOKENS, list)
  },
}

export type SavedCourse = {
  key: string
  title: string
  start: StartIn
  start_label: string
  location_ids: number[]
  fixed_order: boolean
  saved_at: string
  course_id?: number | null // 추천 코스를 저장했으면 그 번호(완주 배지를 추천 코스로 받는다)
}

export const savedCourses = {
  all(): SavedCourse[] {
    const v = store.get<unknown>(COURSES, [])
    return Array.isArray(v) ? (v as SavedCourse[]) : []
  },
  get(key: string): SavedCourse | undefined {
    return savedCourses.all().find((c) => c.key === key)
  },
  /** 저장. 이미 10개면 저장하지 않고 'full'을 돌려준다(오래된 것을 몰래 지우지 않는다). */
  save(c: Omit<SavedCourse, 'key' | 'saved_at'>): { ok: true; key: string } | { ok: false; reason: 'full' | 'storage' } {
    const list = savedCourses.all()
    if (list.length >= MAX_SAVED_COURSES) return { ok: false, reason: 'full' }
    const key = `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
    const ok = store.set(COURSES, [{ ...c, key, saved_at: new Date().toISOString() }, ...list])
    return ok ? { ok: true, key } : { ok: false, reason: 'storage' }
  },
  remove(key: string): void {
    store.set(
      COURSES,
      savedCourses.all().filter((c) => c.key !== key),
    )
  },
}

/** 현재 위치 출발이면 좌표를 저장하지 않는다(BR-10). 다시 열 때 그때의 현재 위치를 다시 받는다. */
export function startForSaving(start: StartIn): StartIn {
  return start.type === 'current' ? { type: 'current' } : start
}
