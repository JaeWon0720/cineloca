// UI-07 코스 상세 (FR-402·405·406·413, FR-508): 동선 지도, 방문 순서와 구간 이동(도보·도시철도), 총 소요 시간,
// 순서 직접 바꾸기, 브라우저에 저장, 방문 인증 진행과 완주 배지, 주변 시설 추천.
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { api } from '../api/client.ts'
import type { CourseLeg, CoursePlan, StartIn } from '../api/courses.ts'
import type { BadgeResponse, CourseRef, VerifyResponse } from '../api/stamps.ts'
import { fmtDist, fmtMin, stationLabel } from '../lib/geo.ts'
import { MAX_SAVED_COURSES, savedCourses, startForSaving, tokens } from '../lib/wallet.ts'
import { Amenities } from './Amenities.tsx'
import MapView from './MapView.tsx'
import { StampButton } from './StampButton.tsx'

function LegLine({ leg }: { leg: CourseLeg }) {
  if (leg.mode === 'subway' && leg.board && leg.alight)
    return (
      <div className="leg subway">
        <span className="leg-mode">도시철도 {leg.minutes}분</span>
        <span className="small">
          {stationLabel(leg.board.name)}({leg.board.line}) 승차 → {stationLabel(leg.alight.name)}({leg.alight.line}) 하차
        </span>
        <span className="small muted">
          걷기 {leg.walk_to_board_min ?? 0}분 + 타기 {leg.ride_min ?? 0}분 + 걷기 {leg.walk_from_alight_min ?? 0}분
          {leg.board.line !== leg.alight.line && ' · 환승 포함'}
        </span>
      </div>
    )
  return (
    <div className="leg walk">
      <span className="leg-mode">걸어서 {leg.minutes}분</span>
      <span className="small muted">직선거리 {fmtDist(leg.distance_m)}</span>
    </div>
  )
}

export function CourseView({
  plan,
  title,
  courseId,
  start,
  editable = false,
  onReorder,
  savedKey,
}: {
  plan: CoursePlan
  title: string
  courseId?: number | null // 추천 코스면 번호(완주 배지를 추천 코스로 받는다)
  start: StartIn // 저장할 때 쓴다. 현재 위치는 좌표 없이 저장한다(BR-10)
  editable?: boolean
  onReorder?: (ids: number[]) => void
  savedKey?: string // 저장한 코스에서 열었으면 그 키
}) {
  const [certified, setCertified] = useState<Set<number>>(new Set())
  const [saveMsg, setSaveMsg] = useState<string | null>(null)
  const [badgeMsg, setBadgeMsg] = useState<string | null>(null)
  const [badgeBusy, setBadgeBusy] = useState(false)
  const ids = useMemo(() => plan.stops.map((s) => s.id), [plan])
  const targets = plan.stops.filter((s) => s.cert.eligible)
  const done = targets.filter((s) => certified.has(s.id)).length
  const ref: CourseRef = courseId ? { id: courseId } : { location_ids: ids }

  const refresh = useCallback(() => {
    const held = tokens.all()
    if (!held.length) return
    api
      .post<VerifyResponse>('/stamps/verify', { tokens: held })
      .then((v) => setCertified(new Set(v.valid.map((x) => x.location.id))))
      .catch(() => null)
  }, [])
  useEffect(refresh, [refresh])

  const path = useMemo<[number, number][]>(() => {
    const pts: [number, number][] = plan.stops.map((s) => [s.lat, s.lon])
    if (plan.start.lat != null && plan.start.lon != null && plan.start.type !== 'location') pts.unshift([plan.start.lat, plan.start.lon])
    return pts
  }, [plan])
  const markers = useMemo(() => plan.stops.map((s) => ({ id: s.id, lat: s.lat, lon: s.lon, label: s.name, order: s.order })), [plan])

  function move(i: number, d: -1 | 1) {
    const next = [...ids]
    ;[next[i], next[i + d]] = [next[i + d], next[i]]
    onReorder?.(next)
  }

  function save() {
    const r = savedCourses.save({
      title,
      start: startForSaving(start),
      start_label: plan.start.label,
      location_ids: ids,
      fixed_order: !plan.optimized,
      course_id: courseId ?? null,
    })
    setSaveMsg(
      r.ok
        ? '이 브라우저에 저장했어요. 내 기록 > 저장한 코스에서 다시 열 수 있어요.'
        : r.reason === 'full'
          ? `코스는 ${MAX_SAVED_COURSES}개까지 저장할 수 있어요. 내 기록에서 하나를 지운 뒤 다시 저장해 주세요.`
          : '이 브라우저에 저장하지 못했어요. 시크릿 창이면 일반 창에서 다시 해 주세요.',
    )
  }

  async function claimBadge() {
    setBadgeBusy(true)
    setBadgeMsg(null)
    try {
      const r = await api.post<BadgeResponse>('/badges', { course: ref, stamps: tokens.all() })
      if (r.ok && r.token) {
        tokens.add(r.token)
        setBadgeMsg('완주 배지를 받았어요. 내 기록에서 볼 수 있어요.')
      } else if (r.reason === 'too_few_targets') setBadgeMsg('인증 대상이 2곳 이상인 코스만 완주 배지를 받을 수 있어요.')
      else setBadgeMsg(`아직 인증하지 않은 곳이 ${r.missing.length}곳 있어요.`)
    } catch (e) {
      setBadgeMsg(e instanceof Error ? e.message : '배지를 받지 못했어요.')
    } finally {
      setBadgeBusy(false)
    }
  }

  const legTo = (order: number) => plan.legs.find((l) => l.to_order === order)

  return (
    <div className="course">
      <div className="course-sum">
        <div>
          <strong className="big">{fmtMin(plan.total_min)}</strong>
          <span className="small muted">
            {' '}
            이동 {fmtMin(plan.travel_min)} + 머무는 시간 {fmtMin(plan.stay_min)}
          </span>
        </div>
        <span className="meta">
          <span>{plan.stops.length}곳</span>
          <span>총 이동 {fmtDist(plan.total_distance_m)}</span>
          <span>{plan.optimized ? '이동이 짧은 순서로 정리' : courseId && !savedKey ? '추천 순서' : '내가 정한 순서'}</span>
        </span>
        {!plan.subway_data && <p className="small muted">역간 소요시간 자료가 아직 없어 모든 구간을 걷는 시간으로 계산했어요.</p>}
      </div>

      <MapView center={[plan.stops[0].lat, plan.stops[0].lon]} markers={markers} path={path} height={280} />

      <ol className="timeline">
        <li className="tl-start">
          <span className="dot" aria-hidden />
          <span>출발 · {plan.start.label}</span>
        </li>
        {plan.stops.map((s, i) => {
          const leg = legTo(s.order)
          return (
            <Fragment key={s.id}>
              {leg && (
                <li className="tl-leg">
                  <LegLine leg={leg} />
                </li>
              )}
              <li className="tl-stop">
                <span className="rank">{s.order}</span>
                <div className="stop-body">
                  <div className="loc-top">
                    <Link to={`/locations/${s.id}`}>
                      <strong>{s.name}</strong>
                    </Link>
                    <span className="small muted">머무는 시간 {s.stay_min}분</span>
                  </div>
                  {s.gu && <span className="small muted">{s.gu}</span>}
                  <StampButton locationId={s.id} cert={s.cert} done={certified.has(s.id)} onStamped={refresh} compact />
                  {editable && (
                    <div className="row gap reorder">
                      <button className="btn small" aria-label={`${s.name} 앞으로`} disabled={i === 0} onClick={() => move(i, -1)}>
                        ↑ 앞으로
                      </button>
                      <button className="btn small" aria-label={`${s.name} 뒤로`} disabled={i === plan.stops.length - 1} onClick={() => move(i, 1)}>
                        ↓ 뒤로
                      </button>
                    </div>
                  )}
                </div>
              </li>
            </Fragment>
          )
        })}
      </ol>

      <section className="progress" aria-label="방문 인증 진행">
        <h2 className="sub">방문 인증 {done}/{targets.length}</h2>
        {targets.length < 2 ? (
          <p className="small muted">인증 대상이 2곳 이상인 코스만 완주 배지를 받을 수 있어요.</p>
        ) : (
          <>
            <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={targets.length} aria-valuenow={done}>
              <span style={{ width: `${(done / targets.length) * 100}%` }} />
            </div>
            <button className="btn" disabled={badgeBusy || done < targets.length} onClick={claimBadge}>
              완주 배지 받기
            </button>
          </>
        )}
        {badgeMsg && (
          <p className="small" role="status">
            {badgeMsg}
          </p>
        )}
      </section>

      {!savedKey && (
        <div className="stack">
          <button className="btn primary" onClick={save}>
            이 코스 저장
          </button>
          {saveMsg && (
            <p className="small" role="status">
              {saveMsg}
            </p>
          )}
        </div>
      )}

      <Amenities locationIds={ids} />
    </div>
  )
}
