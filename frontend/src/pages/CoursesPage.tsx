// UI-06 코스 (F4, UC-07·08): 추천 코스 목록(FR-401)과 나만의 코스 만들기(FR-403~405).
// AI 맞춤 코스(취향 문장·시간 예산)는 CR-01로 없어졌다(구현 기준 정정표 11/18). 코스의 AI는 주변 시설 추천만이다.
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { api } from '../api/client.ts'
import {
  MAX_STOPS,
  MIN_STOPS,
  THEME_LABEL,
  type CourseList,
  type CoursePlan,
  type StartIn,
  type StartType,
  type Theme,
} from '../api/courses.ts'
import type { LocationHit, LocationSearchResponse, MetaResponse } from '../api/locations.ts'
import { CourseView } from '../components/CourseView.tsx'
import { Empty, ErrorBox, Loading } from '../components/common.tsx'
import { fmtDist, fmtMin, getPosition } from '../lib/geo.ts'
import './courses.css'

export default function CoursesPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'my' ? 'my' : 'recommended'
  return (
    <section className="page page-courses">
      <header className="page-head">
        <h1>코스</h1>
        <p className="lead small">촬영지를 이어 도는 코스를 보고, 직접 만들어 저장해요.</p>
      </header>
      <div className="seg" role="tablist" aria-label="코스 종류">
        <button role="tab" aria-selected={tab === 'recommended'} className={tab === 'recommended' ? 'on' : ''} onClick={() => setParams({})}>
          추천 코스
        </button>
        <button role="tab" aria-selected={tab === 'my'} className={tab === 'my' ? 'on' : ''} onClick={() => setParams({ tab: 'my' })}>
          나만의 코스 만들기
        </button>
      </div>
      {tab === 'recommended' ? <Recommended onMake={() => setParams({ tab: 'my' })} /> : <Builder />}
    </section>
  )
}

// ---------- FR-401 추천 코스 목록: 테마(작품·배우·지역)별, 장소 수·총 소요 시간·총 이동 거리
function Recommended({ onMake }: { onMake: () => void }) {
  const [theme, setTheme] = useState<Theme | ''>('')
  const [res, setRes] = useState<CourseList | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    api
      .get<CourseList>(`/courses${theme ? `?theme=${theme}` : ''}`)
      .then((r) => alive && (setRes(r), setError(null)))
      .catch((e: Error) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [theme])

  return (
    <div className="stack">
      <div className="row wrap gap" role="group" aria-label="테마">
        {(['', 'work', 'person', 'district'] as const).map((t) => (
          <button key={t || 'all'} className={`chip-btn${theme === t ? ' on' : ''}`} aria-pressed={theme === t} onClick={() => setTheme(t)}>
            {t ? THEME_LABEL[t] : '전체'}
          </button>
        ))}
      </div>
      <ErrorBox error={error} />
      {!res && !error && <Loading rows={3} />}
      {res && res.items.length === 0 && (
        <Empty>
          <p>{theme ? `${THEME_LABEL[theme]} 테마` : '아직 공개된'} 추천 코스가 없어요.</p>
          <button className="btn small" onClick={onMake}>
            나만의 코스 만들기
          </button>
        </Empty>
      )}
      <ul className="list">
        {res?.items.map((c) => (
          <li key={c.id}>
            <Link className="card course-card" to={`/courses/${c.id}`}>
              <span className="theme">{THEME_LABEL[c.theme]}</span>
              <strong>{c.title}</strong>
              <span className="meta">
                <span>{c.stop_count}곳</span>
                <span>약 {fmtMin(c.total_min)}</span>
                <span>이동 {fmtDist(c.total_distance_m)}</span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------- FR-403~405 나만의 코스: 출발지와 촬영지 2~10곳을 고르면 순서를 정해 준다
type Picked = Pick<LocationHit, 'id' | 'name' | 'gu'>

function Builder() {
  const [meta, setMeta] = useState<MetaResponse | null>(null)
  const [area, setArea] = useState('')
  const [hitsOf, setHitsOf] = useState<{ area: string; items: LocationHit[] } | null>(null)
  const hits = hitsOf?.area === area ? hitsOf.items : null // 지역을 바꾸면 새로 불러올 때까지 null
  const [picked, setPicked] = useState<Picked[]>([])
  const [startType, setStartType] = useState<StartType>('station')
  const [stationId, setStationId] = useState('')
  const [startLoc, setStartLoc] = useState<number | null>(null)
  const [plan, setPlan] = useState<CoursePlan | null>(null)
  const [start, setStart] = useState<StartIn | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<MetaResponse>('/meta')
      .then((m) => {
        setMeta(m)
        setArea(m.districts[0]?.name ?? '')
        setStationId(m.stations[0]?.id ?? '')
        if (!m.stations.length) setStartType('current')
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  // 구·군 중심 3km 안 촬영지에서 고른다(좌표 있는 곳만, FR-403)
  useEffect(() => {
    const d = meta?.districts.find((x) => x.name === area)
    if (!d) return
    let alive = true
    api
      .post<LocationSearchResponse>('/locations/search', { base: { lat: d.lat, lon: d.lon }, radius_m: 3000, gu: [d.name] })
      .then((r) => alive && setHitsOf({ area: d.name, items: r.items }))
      .catch((e: Error) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [meta, area])

  const ids = useMemo(() => picked.map((p) => p.id), [picked])
  const full = picked.length >= MAX_STOPS
  const startOk = startType === 'current' || (startType === 'station' && !!stationId) || (startType === 'location' && startLoc != null && ids.includes(startLoc))
  const canMake = picked.length >= MIN_STOPS && picked.length <= MAX_STOPS && startOk && !busy

  function toggle(h: Picked) {
    setPicked((x) => (x.some((p) => p.id === h.id) ? x.filter((p) => p.id !== h.id) : x.length >= MAX_STOPS ? x : [...x, { id: h.id, name: h.name, gu: h.gu }]))
    setPlan(null)
  }

  async function make(order?: number[]) {
    setBusy(true)
    setError(null)
    try {
      let s: StartIn
      if (startType === 'current') {
        const p = await getPosition()
        s = { type: 'current', lat: p.lat, lon: p.lon }
      } else if (startType === 'station') s = { type: 'station', id: stationId }
      else s = { type: 'location', id: String(startLoc) }
      const r = await api.post<CoursePlan>('/courses/plan', { start: s, location_ids: order ?? ids, fixed_order: !!order })
      setStart(s)
      setPlan(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : '코스를 만들지 못했어요.')
    } finally {
      setBusy(false)
    }
  }

  if (!meta) return error ? <ErrorBox error={error} /> : <Loading rows={3} />

  return (
    <div className="builder">
      <fieldset className="field-group">
        <legend>1. 출발지</legend>
        <div className="row wrap gap">
          {(
            [
              ['current', '현재 위치'],
              ['station', '도시철도역'],
              ['location', '고른 촬영지 중 하나'],
            ] as const
          ).map(([v, label]) => (
            <label key={v} className={`toggle${startType === v ? ' on' : ''}`}>
              <input type="radio" name="start" value={v} checked={startType === v} onChange={() => (setStartType(v), setPlan(null))} disabled={v === 'station' && !meta.stations.length} />
              {label}
            </label>
          ))}
        </div>
        {startType === 'station' && (
          <select aria-label="출발역" value={stationId} onChange={(e) => (setStationId(e.target.value), setPlan(null))}>
            {meta.stations.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}({s.line})
              </option>
            ))}
          </select>
        )}
        {startType === 'location' && (
          <select aria-label="출발 촬영지" value={startLoc ?? ''} onChange={(e) => (setStartLoc(e.target.value ? Number(e.target.value) : null), setPlan(null))}>
            <option value="">고른 촬영지에서 선택</option>
            {picked.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
        {startType === 'current' && <p className="small muted">코스를 만들 때 한 번만 위치를 받고 저장하지 않아요.</p>}
        {startType === 'location' && <p className="small muted">출발 촬영지가 첫 방문 장소가 돼요.</p>}
      </fieldset>

      <fieldset className="field-group">
        <legend>
          2. 촬영지 고르기 <span className={`count${full ? ' over' : ''}`}>{picked.length}/{MAX_STOPS}곳</span>
        </legend>
        <p className="small muted">
          {MIN_STOPS}~{MAX_STOPS}곳을 골라요. 위치가 확인된 촬영지만 넣을 수 있어요.
        </p>
        {picked.length > 0 && (
          <ul className="chips picked" aria-label="고른 촬영지">
            {picked.map((p) => (
              <li key={p.id}>
                <span>{p.name}</span>
                <button aria-label={`${p.name} 빼기`} onClick={() => toggle(p)}>
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <label className="field">
          <span>지역</span>
          <select value={area} onChange={(e) => setArea(e.target.value)} aria-label="지역">
            {meta.districts.map((d) => (
              <option key={d.name}>{d.name}</option>
            ))}
          </select>
        </label>
        {!hits && <Loading rows={2} />}
        {hits?.length === 0 && <p className="small muted">이 지역에서 고를 수 있는 촬영지가 없어요. 다른 지역을 골라 보세요.</p>}
        <ul className="pick-list">
          {hits?.map((h) => {
            const on = ids.includes(h.id)
            return (
              <li key={h.id}>
                <label className={`pick-row${on ? ' on' : ''}`}>
                  <input type="checkbox" checked={on} disabled={!on && full} onChange={() => toggle(h)} />
                  <span className="pick-name">{h.name}</span>
                  <span className="small muted">{h.works.slice(0, 2).map((w) => `「${w.title}」`).join(' ')}</span>
                </label>
              </li>
            )
          })}
        </ul>
        {full && <p className="small notice">최대 {MAX_STOPS}곳까지 고를 수 있어요. 다른 곳을 넣으려면 하나를 빼 주세요.</p>}
      </fieldset>

      <button className="btn primary" disabled={!canMake} onClick={() => make()}>
        코스 만들기
      </button>
      {picked.length < MIN_STOPS && <p className="small muted">촬영지를 {MIN_STOPS - picked.length}곳 더 골라 주세요.</p>}
      {startType === 'location' && !startOk && picked.length >= MIN_STOPS && <p className="small muted">출발 촬영지를 골라 주세요.</p>}
      <ErrorBox error={error} />
      {busy && <Loading rows={3} />}
      {plan && start && !busy && (
        <>
          <h2 className="sub">나만의 코스</h2>
          <CourseView plan={plan} title={`${plan.start.label} 출발 ${plan.stops.length}곳`} start={start} editable onReorder={(order) => make(order)} />
        </>
      )}
    </div>
  )
}
