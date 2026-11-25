// UI-12 추천 코스 관리 (FR-614, API-024)
// 테마(작품·배우·지역)를 고르면 서버가 촬영지를 모아 이동 시간이 짧은 순서로 후보를 만든다.
// 관리자는 제목·장소·순서·체류 시간·출발역을 고친 뒤 승인한다. 승인된 코스만 사용자 추천 코스(API-009)에 나온다.
import { useCallback, useEffect, useState } from 'react'
import {
  COURSE_STATUS_LABEL,
  COURSE_STOPS_MAX,
  COURSE_STOPS_MIN,
  type AdminCourseDetail,
  type AdminCourseList,
  type AdminCoursePatch,
  type AdminCourseStatus,
  type CourseCandidateRequest,
} from '../../api/admin.ts'
import { api } from '../../api/client.ts'
import { THEME_LABEL, type Theme } from '../../api/courses.ts'
import type { MetaResponse } from '../../api/locations.ts'
import type { WorkListResponse } from '../../api/works.ts'
import { Empty, ErrorBox, Loading } from '../../components/common.tsx'
import { fmtTime, useAdmin } from '../../lib/admin.ts'
import { fmtDist, fmtMin } from '../../lib/geo.ts'
import './admin-pages.css'

type EditStop = { location_id: number; name: string; stay_min: number }

export function AdminCoursesPage() {
  const { guard } = useAdmin()
  const [meta, setMeta] = useState<MetaResponse | null>(null)
  const [filter, setFilter] = useState<'' | AdminCourseStatus>('')
  const [list, setList] = useState<AdminCourseList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<number | null>(null)

  const load = useCallback(() => {
    const q = filter ? `?status=${filter}` : ''
    return guard(api.get<AdminCourseList>(`/admin/courses${q}`))
      .then((d) => (setList(d), setError(null)))
      .catch((e: Error) => setError(e.message))
  }, [guard, filter])

  useEffect(() => {
    void load()
  }, [load])
  useEffect(() => {
    api
      .get<MetaResponse>('/meta')
      .then(setMeta)
      .catch(() => setMeta(null))
  }, [])

  return (
    <section className="page admin-page">
      <header className="admin-page-head">
        <div>
          <h1>추천 코스 관리</h1>
          <p className="page-desc">테마를 고르면 촬영지를 모아 후보를 만들어 드려요. 고쳐서 승인한 코스만 사용자 화면의 추천 코스에 나옵니다.</p>
        </div>
      </header>

      <CandidateForm
        meta={meta}
        onCreated={(d) => {
          setSelected(d.id)
          void load()
        }}
      />

      <div className="review-filters">
        <div className="seg small" role="group" aria-label="승인 상태">
          {(['', 'draft', 'approved'] as const).map((s) => (
            <button key={s || 'all'} type="button" aria-pressed={filter === s} className={filter === s ? 'on' : ''} onClick={() => setFilter(s)}>
              {s ? COURSE_STATUS_LABEL[s] : '전체'}
            </button>
          ))}
        </div>
      </div>
      <ErrorBox error={error} />
      {!list && !error && <Loading rows={3} />}
      {list && list.items.length === 0 && <Empty>코스가 없어요. 위에서 후보를 만들어 보세요.</Empty>}
      {list && list.items.length > 0 && (
        <ul className="review-list" aria-label="추천 코스 목록">
          {list.items.map((c) => (
            <li key={c.id} className={`admin-card course-row${selected === c.id ? ' on' : ''}`}>
              <button type="button" className="link-like" onClick={() => setSelected(c.id)}>
                <strong>{c.title}</strong>
              </button>
              <span className="kind-pill">{THEME_LABEL[c.theme]}</span>
              <span className={`status-pill status-${c.status}`}>{COURSE_STATUS_LABEL[c.status]}</span>
              <span className="muted small">
                {c.stop_count}곳{c.total_min != null && ` · ${fmtMin(c.total_min)}`}
                {c.total_distance_m != null && ` · ${fmtDist(c.total_distance_m)}`}
              </span>
              {c.problem && <span className="field-error small">{c.problem}</span>}
            </li>
          ))}
        </ul>
      )}
      {selected != null && (
        <CourseEditor
          key={selected}
          id={selected}
          meta={meta}
          onChanged={() => void load()}
          onDeleted={() => {
            setSelected(null)
            void load()
          }}
        />
      )}
    </section>
  )
}

// ───────────────────────── 후보 만들기 (POST /admin/courses)
function CandidateForm({ meta, onCreated }: { meta: MetaResponse | null; onCreated: (d: AdminCourseDetail) => void }) {
  const { guard } = useAdmin()
  const [theme, setTheme] = useState<Theme>('district')
  const [ref, setRef] = useState('')
  const [q, setQ] = useState('')
  const [works, setWorks] = useState<WorkListResponse['items']>([])
  const [maxStops, setMaxStops] = useState(5)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function searchWorks() {
    if (!q.trim()) return
    try {
      setWorks((await api.get<WorkListResponse>(`/works?q=${encodeURIComponent(q.trim())}&size=5`)).items)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const body: CourseCandidateRequest = { theme, theme_ref: ref, max_stops: maxStops }
      onCreated(await guard(api.post<AdminCourseDetail>('/admin/courses', body)))
      setRef('')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      className="admin-card course-candidate"
      aria-label="후보 만들기"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <h2>후보 만들기</h2>
      <div className="review-filters">
        <label className="field inline">
          <span>테마</span>
          <select value={theme} onChange={(e) => (setTheme(e.target.value as Theme), setRef(''), setWorks([]))}>
            <option value="district">지역</option>
            <option value="work">작품</option>
            <option value="person">배우</option>
          </select>
        </label>
        {theme === 'district' && (
          <label className="field inline">
            <span>구·군</span>
            <select value={ref} onChange={(e) => setRef(e.target.value)}>
              <option value="">고르세요</option>
              {meta?.districts.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {theme === 'work' && (
          <>
            <label className="field inline">
              <span>작품 찾기</span>
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="작품명" onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), void searchWorks())} />
            </label>
            <button type="button" className="btn small" onClick={() => void searchWorks()}>
              찾기
            </button>
            {works.length > 0 && (
              <label className="field inline">
                <span>작품</span>
                <select value={ref} onChange={(e) => setRef(e.target.value)}>
                  <option value="">고르세요</option>
                  {works.map((w) => (
                    <option key={w.id} value={String(w.id)}>
                      {w.title}
                      {w.release_year ? ` (${w.release_year})` : ''}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </>
        )}
        {theme === 'person' && (
          <label className="field inline">
            <span>인물 번호</span>
            <input value={ref} inputMode="numeric" onChange={(e) => setRef(e.target.value.replace(/\D/g, ''))} />
          </label>
        )}
        <label className="field inline">
          <span>촬영지 수</span>
          <input type="number" min={COURSE_STOPS_MIN} max={COURSE_STOPS_MAX} value={maxStops} onChange={(e) => setMaxStops(Number(e.target.value))} />
        </label>
        <button type="submit" className="btn small primary" disabled={busy || !ref}>
          후보 만들기
        </button>
      </div>
      <ErrorBox error={error} />
    </form>
  )
}

// ───────────────────────── 수정·승인 (GET·PATCH·DELETE /admin/courses/{id})
function CourseEditor({ id, meta, onChanged, onDeleted }: { id: number; meta: MetaResponse | null; onChanged: () => void; onDeleted: () => void }) {
  const { guard } = useAdmin()
  const [d, setD] = useState<AdminCourseDetail | null>(null)
  const [title, setTitle] = useState('')
  const [stops, setStops] = useState<EditStop[]>([])
  const [station, setStation] = useState('')
  const [addId, setAddId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const take = useCallback((x: AdminCourseDetail) => {
    setD(x)
    setTitle(x.title)
    setStops(x.stops.map((s) => ({ location_id: s.id, name: s.name, stay_min: s.stay_min })))
    setStation(x.start_station_id ?? '')
  }, [])

  useEffect(() => {
    guard(api.get<AdminCourseDetail>(`/admin/courses/${id}`))
      .then(take)
      .catch((e: Error) => setError(e.message))
  }, [guard, id, take])

  const dirty = !!d && (title !== d.title || station !== (d.start_station_id ?? '') || JSON.stringify(stops.map((s) => [s.location_id, s.stay_min])) !== JSON.stringify(d.stops.map((s) => [s.id, s.stay_min])))

  async function send(body: AdminCoursePatch, message: string) {
    setBusy(true)
    setError(null)
    setDone(null)
    try {
      take(await guard(api.patch<AdminCourseDetail>(`/admin/courses/${id}`, body)))
      setDone(message)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    try {
      await guard(api.del(`/admin/courses/${id}`))
      onDeleted()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  function move(i: number, by: number) {
    const next = [...stops]
    const [s] = next.splice(i, 1)
    next.splice(i + by, 0, s)
    setStops(next)
  }

  if (!d) return error ? <ErrorBox error={error} /> : <Loading rows={2} />
  const edits: AdminCoursePatch = { title, stops: stops.map((s) => ({ location_id: s.location_id, stay_min: s.stay_min })), start_station_id: station || null }
  const legTo = new Map(d.legs.map((l) => [l.to_order, l]))

  return (
    <article className="admin-card course-editor" aria-label="코스 편집">
      <header className="review-head">
        <h2>코스 편집</h2>
        <span className={`status-pill status-${d.status}`}>
          {COURSE_STATUS_LABEL[d.status]}
          {d.approved_at && ` ${fmtTime(d.approved_at)}`}
        </span>
        <span className="muted small">
          {THEME_LABEL[d.theme]} 테마 · 총 {fmtMin(d.total_min)} (이동 {fmtMin(d.travel_min)} + 체류 {fmtMin(d.stay_min)}) · {fmtDist(d.total_distance_m)}
        </span>
      </header>
      <label className="field">
        <span>제목</span>
        <input value={title} maxLength={60} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="field inline">
        <span>출발</span>
        <select value={station} onChange={(e) => setStation(e.target.value)}>
          <option value="">첫 촬영지에서 출발</option>
          {meta?.stations.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}역({s.line})
            </option>
          ))}
        </select>
      </label>
      <ol className="course-stops">
        {stops.map((s, i) => {
          const leg = !dirty ? legTo.get(i + 1) : undefined
          return (
            <li key={s.location_id}>
              <span className="stop-name">
                {s.name || `촬영지 ${s.location_id}`}
                {leg && <span className="muted small"> · {leg.mode === 'subway' ? '도시철도' : '도보'} {leg.minutes}분</span>}
              </span>
              <label className="field inline">
                <span>체류(분)</span>
                <input type="number" min={0} max={240} value={s.stay_min} onChange={(e) => setStops(stops.map((x, j) => (j === i ? { ...x, stay_min: Number(e.target.value) } : x)))} />
              </label>
              <button type="button" className="btn small" aria-label={`${s.name} 위로`} disabled={i === 0} onClick={() => move(i, -1)}>
                ▲
              </button>
              <button type="button" className="btn small" aria-label={`${s.name} 아래로`} disabled={i === stops.length - 1} onClick={() => move(i, 1)}>
                ▼
              </button>
              <button type="button" className="btn small danger" aria-label={`${s.name} 빼기`} disabled={stops.length <= COURSE_STOPS_MIN} onClick={() => setStops(stops.filter((_, j) => j !== i))}>
                빼기
              </button>
            </li>
          )
        })}
      </ol>
      <div className="review-actions">
        <label className="field inline">
          <span>촬영지 번호로 더하기</span>
          <input value={addId} inputMode="numeric" onChange={(e) => setAddId(e.target.value.replace(/\D/g, ''))} />
        </label>
        <button
          type="button"
          className="btn small"
          disabled={!addId || stops.length >= COURSE_STOPS_MAX || stops.some((s) => s.location_id === Number(addId))}
          onClick={() => (setStops([...stops, { location_id: Number(addId), name: '', stay_min: 20 }]), setAddId(''))}
        >
          더하기
        </button>
      </div>
      {dirty && <p className="muted small">고친 내용을 저장하면 다시 계산되고, 승인된 코스는 승인 전으로 돌아가요.</p>}
      {done && (
        <p className="notice" role="status">
          {done}
        </p>
      )}
      <ErrorBox error={error} />
      <div className="review-actions">
        <button type="button" className="btn small" disabled={busy || !dirty} onClick={() => void send(edits, '저장했어요.')}>
          저장
        </button>
        {d.status === 'draft' ? (
          <button type="button" className="btn small primary" disabled={busy} onClick={() => void send(dirty ? { ...edits, status: 'approved' } : { status: 'approved' }, '승인했어요. 사용자 추천 코스에 나옵니다.')}>
            {dirty ? '저장하고 승인' : '승인'}
          </button>
        ) : (
          <button type="button" className="btn small" disabled={busy} onClick={() => void send({ status: 'draft' }, '승인을 취소했어요.')}>
            승인 취소
          </button>
        )}
        {d.status === 'draft' && (
          <button type="button" className="btn small danger" disabled={busy} onClick={() => void remove()}>
            후보 삭제
          </button>
        )}
      </div>
    </article>
  )
}
