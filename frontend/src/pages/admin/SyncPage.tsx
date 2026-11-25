// UI-10 동기화 리포트 (FR-612·613·616, NFR-005, API-021)
// 지금 동기화를 요청하고, 진행 중인 실행·경고·오늘 외부 호출 수·정기 주기·최근 10회 리포트를 본다.
// 동기화는 배치(--watch)가 1분 안에 가져가 실행하므로, 대기·실행 중이면 15초마다 다시 불러온다.
import { useCallback, useEffect, useState } from 'react'
import {
  SYNC_STATUS_LABEL,
  SYNC_TRIGGER_LABEL,
  type SyncCounts,
  type SyncOverview,
  type SyncReport,
  type SyncRequestResult,
} from '../../api/admin.ts'
import { api } from '../../api/client.ts'
import { Empty, ErrorBox, Loading } from '../../components/common.tsx'
import { fmtTime, useAdmin } from '../../lib/admin.ts'
import './admin-pages.css'

const POLL_MS = 15_000

export function AdminSyncPage() {
  const { guard } = useAdmin()
  const [data, setData] = useState<SyncOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(
    () =>
      guard(api.get<SyncOverview>('/admin/sync'))
        .then((d) => {
          setData(d)
          setError(null)
        })
        .catch((e: Error) => setError(e.message)),
    [guard],
  )

  useEffect(() => {
    void load()
  }, [load])

  const active = data?.active ?? null
  useEffect(() => {
    if (!active) return
    const t = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(t)
  }, [active, load])

  async function requestSync() {
    setBusy(true)
    setNotice(null)
    try {
      const r = await guard(api.post<SyncRequestResult>('/admin/sync', {}))
      setNotice(`${r.message} (실행 #${r.run_id})`)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="page admin-page">
      <header className="admin-page-head">
        <div>
          <h1>동기화 리포트</h1>
          <p className="page-desc">공공데이터를 다시 받아 반영하고, 실행마다 건수·실패를 남깁니다.</p>
        </div>
        <div className="admin-actions">
          <button type="button" className="btn small" onClick={() => void load()}>
            새로 고침
          </button>
          <button type="button" className="btn primary" onClick={() => void requestSync()} disabled={busy || !!active || !data}>
            {busy ? '요청 중…' : '지금 동기화'}
          </button>
        </div>
      </header>
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <ErrorBox error={error} />
      {!data && !error && <Loading rows={4} />}
      {data && (
        <>
          {active && (
            <p className="sync-active" role="status">
              <span className="dot" aria-hidden="true" />
              동기화 #{active.run_id}({SYNC_TRIGGER_LABEL[active.trigger]}) {active.status === 'running' ? `실행 중 · ${fmtTime(active.started_at)} 시작` : `대기 중 · ${fmtTime(active.requested_at)} 요청`}. 끝나면 아래에 리포트가 생겨요.
            </p>
          )}
          {data.alerts.length > 0 && (
            <ul className="admin-alerts" aria-label="경고">
              {data.alerts.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          )}

          <div className="sync-grid">
            <section className="admin-card" aria-labelledby="sync-schedule">
              <h2 id="sync-schedule">정기 동기화</h2>
              {data.schedule.interval_days > 0 ? (
                <dl className="kv">
                  <dt>주기</dt>
                  <dd>{data.schedule.interval_days}일마다</dd>
                  <dt>마지막 시작</dt>
                  <dd>{fmtTime(data.schedule.last_started_at) || '없음'}</dd>
                  <dt>다음 예정</dt>
                  <dd>{dueText(data.schedule.next_due_at)}</dd>
                </dl>
              ) : (
                <p className="muted small">꺼져 있어요(SYNC_INTERVAL_DAYS=0). 필요할 때 '지금 동기화'로 돌려 주세요.</p>
              )}
            </section>

            <section className="admin-card" aria-labelledby="sync-usage">
              <h2 id="sync-usage">오늘 외부 호출 수 ({data.usage_day})</h2>
              {data.usage.length === 0 ? (
                <p className="muted small">오늘은 아직 호출이 없어요.</p>
              ) : (
                <ul className="usage">
                  {data.usage.map((u) => (
                    <li key={u.key} className={`usage-${u.level}`}>
                      <span className="usage-key">{u.key}</span>
                      <span className="usage-num">
                        {u.count.toLocaleString()}
                        {u.limit != null && ` / ${u.limit.toLocaleString()}`}
                      </span>
                      {u.ratio != null && (
                        <span className="usage-bar" role="img" aria-label={`한도의 ${pct(u.ratio)}`}>
                          <span style={{ width: `${Math.min(100, u.ratio * 100)}%` }} />
                        </span>
                      )}
                      {u.level !== 'ok' && <span className="usage-level">{u.level === 'warning' ? '70% 넘음' : '한도 다 씀'}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <h2 className="sub">최근 리포트</h2>
          {data.reports.length === 0 ? (
            <Empty>아직 동기화 기록이 없어요. '지금 동기화'로 첫 실행을 요청해 보세요.</Empty>
          ) : (
            <ol className="sync-reports">
              {data.reports.map((r) => (
                <ReportCard key={r.run_id} r={r} />
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  )
}

function ReportCard({ r }: { r: SyncReport }) {
  const c = r.counts
  return (
    <li className={`admin-card sync-report status-${r.status}`}>
      <header className="sync-report-head">
        <h3>
          #{r.run_id} <span className="muted">{SYNC_TRIGGER_LABEL[r.trigger]}</span>
        </h3>
        <span className={`status-pill status-${r.status}`}>{SYNC_STATUS_LABEL[r.status]}</span>
        <span className="muted small">
          {fmtTime(r.started_at) || '시작 전'}
          {r.finished_at && ` ~ ${fmtTime(r.finished_at)} (${duration(r.started_at, r.finished_at)})`}
        </span>
      </header>
      {r.guard.length > 0 && (
        <ul className="admin-alerts" aria-label="건수 가드">
          {r.guard.map((g) => (
            <li key={g}>{g}</li>
          ))}
        </ul>
      )}
      {c && (
        <dl className="sync-counts">
          <Count label="작품" now={c.works} prev={r.prev_counts?.works} />
          <Count label="촬영지" now={c.locations} prev={r.prev_counts?.locations} />
          <Count label="KMDb 연결" now={c.match.matched} prev={r.prev_counts?.match.matched} rate={r.rates?.match_rate} />
          <Count label="검수 대기" now={c.match.review} prev={r.prev_counts?.match.review} rate={r.rates?.review_rate} />
          <Count label="좌표 확보" now={c.geo.located} prev={r.prev_counts?.geo.located} rate={r.rates?.geo_rate} />
        </dl>
      )}
      {c && Object.keys(c.fetched).length > 0 && <p className="muted small">수집: {fetchedText(c)}</p>}
      {r.warnings.length > 0 && (
        <ul className="sync-warnings">
          {r.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {r.failures.length > 0 && (
        <details className="sync-failures">
          <summary>실패 {r.failures.length}건 보기</summary>
          <ul>
            {r.failures.map((f, i) => (
              <li key={i}>
                <strong>{[f.dataset, f.step].filter(Boolean).join(' · ')}</strong> {f.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  )
}

function Count({ label, now, prev, rate }: { label: string; now: number; prev?: number; rate?: number | null }) {
  const d = prev == null ? null : now - prev
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        <strong>{now.toLocaleString()}</strong>
        {rate != null && <span className="muted"> ({pct(rate)})</span>}
        {d != null && d !== 0 && <span className={`delta ${d > 0 ? 'up' : 'down'}`}>{d > 0 ? `+${d.toLocaleString()}` : d.toLocaleString()}</span>}
      </dd>
    </div>
  )
}

const fetchedText = (c: SyncCounts) =>
  Object.entries(c.fetched)
    .map(([k, v]) => `${k} ${v.toLocaleString()}건`)
    .join(', ')

const pct = (r: number) => `${Math.round(r * 1000) / 10}%`

/** 다음 정기 실행: 이미 지난 시각이면 배치가 곧 가져가므로 '곧 시작' */
function dueText(iso: string | null): string {
  if (!iso || new Date(iso).getTime() <= Date.now()) return '곧 시작'
  return fmtTime(iso)
}

function duration(a: string | null, b: string | null): string {
  if (!a || !b) return ''
  const m = Math.round((new Date(b).getTime() - new Date(a).getTime()) / 60000)
  return m < 1 ? '1분 미만' : `${m}분`
}
