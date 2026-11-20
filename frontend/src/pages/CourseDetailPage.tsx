// UI-07 코스 상세: 추천 코스(/courses/:id, API-010)와 저장한 코스(/courses/saved/:key).
// 저장한 코스는 순서·출발지만 브라우저에 있고, 열 때마다 지금 데이터로 다시 계산한다(추천 코스와 같은 원칙, 정정표 11/18).
// 저장한 추천 코스는 API-010으로 다시 받아 장소별 체류 시간을 지키고, 그 코스가 없어졌으면(404) API-011로 계산한다.
import { useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { api, ApiError } from '../api/client.ts'
import { THEME_LABEL, type CourseDetail, type CoursePlan, type StartIn } from '../api/courses.ts'
import { CourseView } from '../components/CourseView.tsx'
import { BackLink, ErrorBox, Loading } from '../components/common.tsx'
import { getPosition } from '../lib/geo.ts'
import { savedCourses } from '../lib/wallet.ts'
import './courses.css'

export default function CourseDetailPage() {
  const { id = '', key = '' } = useParams()
  type Loaded = { plan: CoursePlan; title: string; start: StartIn; courseId: number | null; theme?: string }
  const k = key ? `saved:${key}` : `course:${id}`
  // 다른 코스로 옮기면 이전 결과를 보여 주지 않도록 주소별로 결과를 묶어 둔다
  const [result, setResult] = useState<{ k: string; state: Loaded | null; error: string | null } | null>(null)
  const state = result?.k === k ? result.state : null
  const error = result?.k === k ? result.error : null

  useEffect(() => {
    let alive = true
    async function load() {
      if (key) {
        const c = savedCourses.get(key)
        if (!c) throw new Error('저장한 코스를 찾지 못했어요. 브라우저 데이터를 지웠다면 다시 만들어 주세요.')
        if (c.course_id != null) {
          try {
            const d = await api.get<CourseDetail>(`/courses/${c.course_id}`)
            return { plan: d, title: c.title, start: { type: 'location' as const, id: String(d.stops[0]?.id ?? '') }, courseId: d.id, theme: THEME_LABEL[d.theme] }
          } catch (e) {
            if (!(e instanceof ApiError && e.status === 404)) throw e
          }
        }
        let start = c.start
        if (start.type === 'current') {
          const p = await getPosition()
          start = { type: 'current', lat: p.lat, lon: p.lon }
        }
        const plan = await api.post<CoursePlan>('/courses/plan', { start, location_ids: c.location_ids, fixed_order: c.fixed_order })
        return { plan, title: c.title, start, courseId: c.course_id ?? null }
      }
      const d = await api.get<CourseDetail>(`/courses/${id}`)
      const first = d.stops[0]
      return {
        plan: d,
        title: d.title,
        start: { type: 'location' as const, id: String(first?.id ?? '') },
        courseId: d.id,
        theme: THEME_LABEL[d.theme],
      }
    }
    load()
      .then((s) => alive && setResult({ k, state: s, error: null }))
      .catch((e: Error) => alive && setResult({ k, state: null, error: e.message }))
    return () => {
      alive = false
    }
  }, [id, key, k])

  return (
    <section className="page page-courses">
      <BackLink to={key ? '/me?tab=courses' : '/courses'}>{key ? '저장한 코스' : '추천 코스'}</BackLink>
      <ErrorBox error={error} />
      {!state && !error && <Loading rows={4} />}
      {state && (
        <>
          <header className="page-head">
            {state.theme && <span className="theme">{state.theme} 테마</span>}
            <h1>{state.title}</h1>
          </header>
          <CourseView plan={state.plan} title={state.title} start={state.start} courseId={state.courseId} savedKey={key || undefined} />
        </>
      )}
    </section>
  )
}
