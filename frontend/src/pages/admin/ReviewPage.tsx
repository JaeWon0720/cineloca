// UI-11 검수 큐·촬영지 속성 편집 (FR-608~610, BR-04·06·07, API-022·023)
// 검수 큐: C1이 넣은 작품 매칭·촬영지 태그 제안을 승인·수정·반려한다. 승인된 것만 서비스에 반영된다(BR-04).
// 속성 편집: 촬영지의 주의 구분(사유지·주거지·학교)·안전 안내·인증 반경(50~300m)·인증 제외·태그를 고친다.
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import {
  CAUTION_LABEL,
  CERT_RADIUS_MAX,
  CERT_RADIUS_MIN,
  PLACE_TAGS,
  REVIEW_STATUS_LABEL,
  SAFETY_NOTE_MAX,
  type AdminLocationAttrs,
  type AdminLocationPatch,
  type MatchProposal,
  type ReviewAction,
  type ReviewItem,
  type ReviewListResponse,
  type TagProposal,
} from '../../api/admin.ts'
import { api } from '../../api/client.ts'
import type { Caution } from '../../api/locations.ts'
import { Empty, ErrorBox, Loading } from '../../components/common.tsx'
import { fmtTime, useAdmin } from '../../lib/admin.ts'
import './admin-pages.css'

const PAGE_SIZE = 20
const VERDICT_LABEL: Record<NonNullable<MatchProposal['verdict']>, string> = { match: '같은 작품', not_listed: 'KMDb에 없음', unsure: '판단 어려움' }

export function AdminReviewPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'attrs' ? 'attrs' : 'queue'
  const locId = params.get('id')

  return (
    <section className="page admin-page">
      <header className="admin-page-head">
        <div>
          <h1>검수 큐·속성 편집</h1>
          <p className="page-desc">자동으로 정하지 못한 작품 연결·장소 태그를 사람이 확인하고, 촬영지의 주의·인증 속성을 고칩니다.</p>
        </div>
      </header>
      <div className="seg" role="tablist" aria-label="검수 화면">
        <button type="button" role="tab" aria-selected={tab === 'queue'} className={tab === 'queue' ? 'on' : ''} onClick={() => setParams({})}>
          검수 큐
        </button>
        <button type="button" role="tab" aria-selected={tab === 'attrs'} className={tab === 'attrs' ? 'on' : ''} onClick={() => setParams({ tab: 'attrs' })}>
          촬영지 속성
        </button>
      </div>
      {tab === 'queue' ? <ReviewQueue /> : <AttrEditor key={locId ?? ''} initialId={locId} />}
    </section>
  )
}

// ───────────────────────── 검수 큐 (API-022)
function ReviewQueue() {
  const { guard } = useAdmin()
  const [status, setStatus] = useState<'pending' | 'handled'>('pending')
  const [kind, setKind] = useState<'' | 'match' | 'tag'>('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<ReviewListResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const load = useCallback(() => {
    const q = new URLSearchParams({ status, page: String(page), size: String(PAGE_SIZE) })
    if (kind) q.set('kind', kind)
    return guard(api.get<ReviewListResponse>(`/admin/reviews?${q}`))
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
  }, [guard, status, kind, page])

  useEffect(() => {
    void load()
  }, [load])

  async function act(r: ReviewItem, body: ReviewAction) {
    setDone(null)
    try {
      const u = await guard(api.patch<ReviewItem>(`/admin/reviews/${r.id}`, body))
      setDone(`${REVIEW_STATUS_LABEL[u.status]}했어요: ${u.target_name}`)
      await load()
      return null
    } catch (e) {
      return (e as Error).message
    }
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1
  return (
    <div className="review-queue">
      <div className="review-filters">
        <div className="seg small" role="group" aria-label="처리 상태">
          <button type="button" aria-pressed={status === 'pending'} className={status === 'pending' ? 'on' : ''} onClick={() => (setStatus('pending'), setPage(1))}>
            대기 {data ? data.counts.pending : ''}
          </button>
          <button type="button" aria-pressed={status === 'handled'} className={status === 'handled' ? 'on' : ''} onClick={() => (setStatus('handled'), setPage(1))}>
            처리됨 {data ? data.counts.handled : ''}
          </button>
        </div>
        <label className="field inline">
          <span>종류</span>
          <select value={kind} onChange={(e) => (setKind(e.target.value as typeof kind), setPage(1))}>
            <option value="">전체</option>
            <option value="match">작품 매칭</option>
            <option value="tag">촬영지 태그</option>
          </select>
        </label>
      </div>
      {done && (
        <p className="notice" role="status">
          {done}
        </p>
      )}
      <ErrorBox error={error} />
      {!data && !error && <Loading rows={3} />}
      {data && data.items.length === 0 && <Empty>{status === 'pending' ? '검수할 항목이 없어요.' : '처리한 항목이 없어요.'}</Empty>}
      {data && data.items.length > 0 && (
        <ul className="review-list">
          {data.items.map((r) => (
            <ReviewRow key={`${r.id}-${r.status}`} r={r} onAct={(b) => act(r, b)} />
          ))}
        </ul>
      )}
      {data && pages > 1 && (
        <nav className="pager" aria-label="검수 큐 쪽">
          <button type="button" className="btn small" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            이전
          </button>
          <span>
            {page} / {pages}
          </span>
          <button type="button" className="btn small" disabled={page >= pages} onClick={() => setPage(page + 1)}>
            다음
          </button>
        </nav>
      )}
    </div>
  )
}

function ReviewRow({ r, onAct }: { r: ReviewItem; onAct: (b: ReviewAction) => Promise<string | null> }) {
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isTag = r.kind === 'tag'
  const tagP = r.proposal as TagProposal
  const matchP = r.proposal as MatchProposal
  const noTags = isTag && tagP.tags.length === 0

  async function run(b: ReviewAction) {
    setBusy(true)
    setError(await onAct(b))
    setBusy(false)
  }

  return (
    <li className={`admin-card review-item status-${r.status}`}>
      <header className="review-head">
        <span className={`kind-pill kind-${r.kind}`}>{isTag ? '촬영지 태그' : '작품 매칭'}</span>
        <Link to={isTag ? `/locations/${r.target_id}` : `/works/${r.target_id}`} className="review-target">
          {r.target_name}
          {r.target_year != null && ` (${r.target_year})`}
        </Link>
        <span className="muted small">
          {r.proposer === 'ai' ? 'AI 제안' : '규칙'}
          {r.score != null && ` · 점수 ${r.score.toFixed(2)}`}
        </span>
        {r.status !== 'pending' && (
          <span className={`status-pill status-${r.status}`}>
            {REVIEW_STATUS_LABEL[r.status]} {fmtTime(r.handled_at)}
          </span>
        )}
      </header>

      {isTag ? (
        tagP.error ? (
          <p className="muted small">AI가 답하지 못해 제안이 없어요. '수정'으로 태그를 골라 주세요.</p>
        ) : (
          <p className="review-proposal">
            제안 태그:{' '}
            {tagP.tags.map((t) => (
              <span key={t} className="chip">
                {t}
              </span>
            ))}
            {tagP.reason && <span className="muted small"> — {tagP.reason}</span>}
          </p>
        )
      ) : (
        <p className="review-proposal">
          {matchP.verdict && <strong>AI 판단: {VERDICT_LABEL[matchP.verdict]} </strong>}
          {matchP.kmdb_id ? (
            <>
              KMDb 후보 <strong>{matchP.kmdb_title ?? matchP.title}</strong>
              {(matchP.prod_year || matchP.year) && ` (${matchP.prod_year ?? matchP.year})`} · {matchP.kmdb_id}
              {matchP.rel_date && <span className="muted small"> · 개봉 {fmtYmd(matchP.rel_date)}</span>}
            </>
          ) : (
            <span className="muted">KMDb 후보 없음</span>
          )}
          {matchP.reason && <span className="muted small"> — {matchP.reason}</span>}
        </p>
      )}

      {r.status === 'pending' && !editing && (
        <div className="review-actions">
          <button type="button" className="btn small primary" disabled={busy || noTags} onClick={() => void run({ action: 'approve' })}>
            승인
          </button>
          <button type="button" className="btn small" disabled={busy} onClick={() => setEditing(true)}>
            수정
          </button>
          <button type="button" className="btn small danger" disabled={busy} onClick={() => void run({ action: 'reject' })}>
            반려
          </button>
          {isTag && (
            <Link className="small" to={`/admin/review?tab=attrs&id=${r.target_id}`}>
              이 촬영지 속성 편집
            </Link>
          )}
        </div>
      )}
      {editing &&
        (isTag ? (
          <TagEditor initial={tagP.tags} busy={busy} onCancel={() => setEditing(false)} onSave={(tags) => void run({ action: 'modify', tags })} />
        ) : (
          <KmdbEditor initial={matchP.kmdb_id ?? ''} busy={busy} onCancel={() => setEditing(false)} onSave={(kmdb_id) => void run({ action: 'modify', kmdb_id })} />
        ))}
      <ErrorBox error={error} />
    </li>
  )
}

function TagEditor({ initial, busy, onCancel, onSave }: { initial: string[]; busy: boolean; onCancel: () => void; onSave: (tags: string[]) => void }) {
  const [tags, setTags] = useState<string[]>(initial)
  return (
    <form
      className="review-edit"
      onSubmit={(e) => {
        e.preventDefault()
        onSave(tags)
      }}
    >
      <TagPicker value={tags} onChange={setTags} legend="태그 고르기 (장소 유형 20종)" />
      <div className="review-actions">
        <button type="submit" className="btn small primary" disabled={busy || tags.length === 0}>
          고친 태그로 승인
        </button>
        <button type="button" className="btn small" onClick={onCancel}>
          취소
        </button>
      </div>
    </form>
  )
}

function KmdbEditor({ initial, busy, onCancel, onSave }: { initial: string; busy: boolean; onCancel: () => void; onSave: (id: string) => void }) {
  const [id, setId] = useState(initial)
  const valid = /^K\d{5}$/.test(id.trim())
  return (
    <form
      className="review-edit"
      onSubmit={(e) => {
        e.preventDefault()
        if (valid) onSave(id.trim())
      }}
    >
      <label className="field">
        <span>연결할 KMDb 번호 (K와 숫자 5자리)</span>
        <input value={id} onChange={(e) => setId(e.target.value.toUpperCase())} maxLength={6} placeholder="K24589" aria-invalid={!!id && !valid} />
      </label>
      <div className="review-actions">
        <button type="submit" className="btn small primary" disabled={busy || !valid}>
          고친 번호로 승인
        </button>
        <button type="button" className="btn small" onClick={onCancel}>
          취소
        </button>
      </div>
    </form>
  )
}

function TagPicker({ value, onChange, legend }: { value: string[]; onChange: (v: string[]) => void; legend: string }) {
  return (
    <fieldset className="tag-picker">
      <legend>{legend}</legend>
      {PLACE_TAGS.map((t) => (
        <label key={t} className={`toggle${value.includes(t) ? ' on' : ''}`}>
          <input type="checkbox" checked={value.includes(t)} onChange={(e) => onChange(e.target.checked ? [...value, t] : value.filter((x) => x !== t))} />
          <span>{t}</span>
        </label>
      ))}
    </fieldset>
  )
}

// ───────────────────────── 촬영지 속성 편집 (API-023)
function AttrEditor({ initialId }: { initialId: string | null }) {
  const { guard } = useAdmin()
  const [, setParams] = useSearchParams()
  const [idText, setIdText] = useState(initialId ?? '')
  const [loc, setLoc] = useState<AdminLocationAttrs | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!initialId) return
    guard(api.get<AdminLocationAttrs>(`/admin/locations/${initialId}`))
      .then((d) => {
        setLoc(d)
        setError(null)
      })
      .catch((e: Error) => {
        setLoc(null)
        setError(e.message)
      })
  }, [guard, initialId])

  function open(e: FormEvent) {
    e.preventDefault()
    const id = idText.trim()
    if (/^\d+$/.test(id)) setParams({ tab: 'attrs', id })
  }

  return (
    <div className="attr-editor">
      <form className="attr-find" onSubmit={open}>
        <label className="field inline">
          <span>촬영지 번호</span>
          <input inputMode="numeric" value={idText} onChange={(e) => setIdText(e.target.value.replace(/\D/g, ''))} placeholder="예: 5" />
        </label>
        <button type="submit" className="btn small" disabled={!idText}>
          불러오기
        </button>
        <span className="muted small">사용자 화면 촬영지 주소(/locations/번호)의 번호예요. 검수 큐의 태그 항목에서도 열 수 있어요.</span>
      </form>
      <ErrorBox error={error} />
      {loc && <AttrForm key={`${loc.id}`} loc={loc} onSaved={setLoc} />}
    </div>
  )
}

function AttrForm({ loc, onSaved }: { loc: AdminLocationAttrs; onSaved: (l: AdminLocationAttrs) => void }) {
  const { guard } = useAdmin()
  const [caution, setCaution] = useState<Caution | null>(loc.caution)
  const [note, setNote] = useState(loc.safety_note ?? '')
  const [radius, setRadius] = useState(String(loc.cert_radius_m))
  const [excluded, setExcluded] = useState(loc.cert_excluded)
  const [tags, setTags] = useState<string[]>(loc.tags)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const r = Number(radius)
  const radiusOk = /^\d+$/.test(radius) && r >= CERT_RADIUS_MIN && r <= CERT_RADIUS_MAX
  const patch: AdminLocationPatch = {}
  if (caution !== loc.caution) patch.caution = caution
  if ((note.trim() || null) !== loc.safety_note) patch.safety_note = note.trim() || null
  if (radiusOk && r !== loc.cert_radius_m) patch.cert_radius_m = r
  if (excluded !== loc.cert_excluded) patch.cert_excluded = excluded
  if (tags.length !== loc.tags.length || tags.some((t) => !loc.tags.includes(t))) patch.tags = tags
  const changed = Object.keys(patch).length > 0

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!radiusOk) {
      setError(`인증 반경은 ${CERT_RADIUS_MIN}~${CERT_RADIUS_MAX}m 사이 정수로 적어 주세요.`)
      return
    }
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      onSaved(await guard(api.patch<AdminLocationAttrs>(`/admin/locations/${loc.id}`, patch)))
      setSaved(true)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const willExclude = excluded || caution !== null || !loc.located
  return (
    <form className="admin-card attr-form" onSubmit={save}>
      <header>
        <h2>
          <Link to={`/locations/${loc.id}`}>{loc.name}</Link> <span className="muted small">#{loc.id}</span>
        </h2>
        <p className="muted small">{[loc.gu, loc.address].filter(Boolean).join(' · ') || '주소 없음'}</p>
        {!loc.located && <p className="notice">좌표가 없어 지도에 안 보이고 방문 인증 대상도 아니에요(FR-210).</p>}
      </header>

      <fieldset className="radio-row">
        <legend>주의 구분 (FR-209)</legend>
        {([null, 'private', 'residential', 'school'] as const).map((c) => (
          <label key={c ?? 'none'} className={`toggle${caution === c ? ' on' : ''}`}>
            <input type="radio" name="caution" checked={caution === c} onChange={() => setCaution(c)} />
            <span>{c ? CAUTION_LABEL[c] : '없음'}</span>
          </label>
        ))}
      </fieldset>

      <label className="field">
        <span>
          안전 안내 ({note.length}/{SAFETY_NOTE_MAX}자)
        </span>
        <textarea rows={2} maxLength={SAFETY_NOTE_MAX} value={note} onChange={(e) => setNote(e.target.value)} placeholder="예: 차가 다니는 길이라 촬영 때 주의해 주세요" />
      </label>

      <div className="attr-row">
        <label className="field">
          <span>
            인증 반경 ({CERT_RADIUS_MIN}~{CERT_RADIUS_MAX}m, BR-07)
          </span>
          <input type="number" min={CERT_RADIUS_MIN} max={CERT_RADIUS_MAX} step={10} value={radius} onChange={(e) => setRadius(e.target.value)} aria-invalid={!radiusOk} />
        </label>
        <label className={`toggle attr-excluded${excluded ? ' on' : ''}`}>
          <input type="checkbox" checked={excluded} onChange={(e) => setExcluded(e.target.checked)} />
          <span>방문 인증에서 제외 (BR-06)</span>
        </label>
      </div>
      {!radiusOk && <p className="field-error">인증 반경은 {CERT_RADIUS_MIN}~{CERT_RADIUS_MAX}m 사이 정수예요.</p>}
      <p className="muted small">{willExclude ? '저장하면 이 촬영지는 방문 인증 대상이 아니에요(주의 구분·인증 제외·좌표 없음).' : `저장하면 반경 ${radiusOk ? r : '—'}m 안에서 방문 인증할 수 있어요.`}</p>

      <TagPicker value={tags} onChange={setTags} legend="장소 유형 태그 (승인된 태그만 화면에 보여요)" />

      <div className="review-actions">
        <button type="submit" className="btn primary" disabled={busy || !changed}>
          {busy ? '저장 중…' : '저장'}
        </button>
        {saved && !changed && (
          <span className="muted small" role="status">
            저장했어요.
          </span>
        )}
      </div>
      <ErrorBox error={error} />
    </form>
  )
}

/** '20230301' → '2023-03-01' */
function fmtYmd(s: string): string {
  return /^\d{8}$/.test(s) ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}` : s
}
