// UI-05 AI 탐색 (F3, UC-05·06, FR-301~312): 말로 묻고, 해석한 조건을 칩으로 확인하고, 결과를 목록·지도로 본다.
// AI가 답하지 못하거나 찾지 못하면 같은 화면에 필터 패널을 열어 AI 없이 찾게 한다(FR-310·311).
// API는 아직 main에 없다(R2, 브랜치 feature/65-f4-query-api). 응답 타입은 그 브랜치 스키마를 옮긴 초안이다.
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { api } from '../api/client.ts'
import type { MetaResponse } from '../api/locations.ts'
import { EMPTY_FILTERS, QUERY_MAX, type ChipKey, type Filters, type QueryResponse, type SearchResponse } from '../api/query.ts'
import MapView from '../components/MapView.tsx'
import { AiBadge } from '../components/ai.tsx'
import { queryErrorMessage } from '../lib/ai.ts'
import { Empty, ErrorBox, Loading } from '../components/common.tsx'
import { getPosition } from '../lib/geo.ts'
import { useNotice } from '../lib/notice.ts'
import './ask.css'

// FR-301: 예시 질의 3개 이상
const EXAMPLES = ['해운대에서 찍은 영화 알려 줘', '중구에 있는 드라마 촬영지', '2015년 이후 개봉한 범죄 영화 촬영지']
const SESSION_HEADER = 'X-Session-Id'

type Result = (QueryResponse | (SearchResponse & Partial<QueryResponse>)) & { from: 'ai' | 'filter' }

export default function AskPage() {
  const { show } = useNotice()
  const [text, setText] = useState('')
  const [asked, setAsked] = useState<string | null>(null)
  const [res, setRes] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [panel, setPanel] = useState<Filters | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const sid = useRef<string | null>(null) // 서버가 만든 세션 ID. 다음 요청부터 헤더로 보낸다
  const tooLong = text.length > QUERY_MAX

  const headers = () => (sid.current ? { [SESSION_HEADER]: sid.current } : undefined)

  async function run<T extends QueryResponse>(call: () => Promise<T>) {
    setBusy(true)
    setError(null)
    setSelected(null)
    try {
      const r = await call()
      sid.current = r.session_id
      setRes({ ...r, from: 'ai' })
      // AI 장애·찾지 못함이면 필터 패널을 열고, 뽑은 조건이 있으면 미리 채운다(FR-311)
      setPanel(r.status === 'ai_unavailable' || r.status === 'not_found' ? (r.filter_panel ?? EMPTY_FILTERS) : null)
    } catch (e) {
      setError(queryErrorMessage(e, show))
    } finally {
      setBusy(false)
    }
  }

  async function submit(e?: FormEvent, q = text) {
    e?.preventDefault()
    const t = q.trim()
    if (!t || t.length > QUERY_MAX || busy) return
    setAsked(t)
    // '내 위치' 조건에만 쓰는 현재 위치. 못 얻어도 질문은 보낸다(서버가 그 조건만 뺀다)
    const base = /내 ?위치|근처|가까운/.test(t) ? await getPosition(3000).then((p) => ({ lat: p.lat, lon: p.lon })).catch(() => null) : null
    await run(() => api.post<QueryResponse>('/query', { text: t, base }, headers()))
    setText('')
  }

  const choose = (option_id: string, key: string) => run(() => api.post<QueryResponse>('/query/choose', { key, option_id }, headers()))
  const removeChip = (key: ChipKey) => run(() => api.post<QueryResponse>('/query/chips/remove', { key }, headers()))

  async function reset() {
    // FR-308: 새 대화로 맥락을 지운다(API-015)
    if (sid.current) await api.del('/query/session', headers()).catch(() => null)
    sid.current = null
    setRes(null)
    setAsked(null)
    setPanel(null)
    setError(null)
    setText('')
  }

  async function filterSearch(f: Filters) {
    setBusy(true)
    setError(null)
    setSelected(null)
    try {
      const r = await api.post<SearchResponse>('/search', { conditions: f })
      setRes({ ...r, from: 'filter' })
    } catch (e) {
      setError(queryErrorMessage(e, show))
    } finally {
      setBusy(false)
    }
  }

  const markers = useMemo(
    () => (res?.hits ?? []).map((h, i) => ({ id: h.location_id, lat: h.lat, lon: h.lon, label: h.name, order: i + 1, active: h.location_id === selected })),
    [res, selected],
  )
  const centre = useMemo<[number, number]>(() => (markers[0] ? [markers[0].lat, markers[0].lon] : [35.115, 129.041]), [markers])

  return (
    <section className="page page-wide page-ask">
      <header className="page-head">
        <h1>AI 탐색</h1>
        <p className="lead small">작품·배우·지역·역을 말로 물어보세요. 찾은 촬영지는 목록과 지도로 보여 드려요.</p>
      </header>

      <form className="ask-form" onSubmit={submit}>
        <label htmlFor="ask-input" className="visually-hidden">
          질문
        </label>
        <textarea
          id="ask-input"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) submit(e)
          }}
          placeholder="예: 해운대에서 찍은 영화 알려 줘"
          aria-describedby="ask-count ask-privacy"
          aria-invalid={tooLong}
        />
        <div className="ask-bar">
          <span id="ask-count" className={tooLong ? 'count over' : 'count'}>
            {text.length}/{QUERY_MAX}
            {tooLong && ' · 200자까지 쓸 수 있어요'}
          </span>
          <div className="row gap">
            {(res || asked) && (
              <button type="button" className="btn small" onClick={reset}>
                새 대화
              </button>
            )}
            <button className="btn primary small" disabled={!text.trim() || tooLong || busy}>
              묻기
            </button>
          </div>
        </div>
        <p id="ask-privacy" className="small muted">
          이름·전화번호 같은 개인정보는 넣지 마세요. 개인정보로 보이는 내용은 자동으로 가려요.
        </p>
      </form>

      {!res && !busy && (
        <div className="examples" aria-label="예시 질문">
          {EXAMPLES.map((q) => (
            <button key={q} className="chip-btn" onClick={() => submit(undefined, q)}>
              {q}
            </button>
          ))}
          <button className="link small" onClick={() => setPanel(panel ? null : EMPTY_FILTERS)}>
            AI 없이 필터로 찾기
          </button>
        </div>
      )}

      {asked && <p className="asked">“{asked}”</p>}
      {busy && <Loading rows={3} />}
      <ErrorBox error={error} />

      {res && !busy && (
        <div className="ask-result">
          {(res.notices ?? []).map((n) => (
            <p key={n} className="notice small" role="note">
              {n}
            </p>
          ))}
          {!!res.unknown?.length && <p className="notice small">사전에 없는 이름이라 뺐어요: {res.unknown.join(', ')}</p>}

          {res.status === 'choose' &&
            res.choices?.map((c) => (
              <div key={c.key + c.name} className="choose" role="group" aria-label={`${c.name} 고르기`}>
                <p>‘{c.name}’이 여러 개예요. 어느 것인가요?</p>
                <div className="row wrap gap">
                  {c.options.map((o) => (
                    <button key={o.id} className="btn small" onClick={() => choose(o.id, c.key)}>
                      {o.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}

          {res.chips.length > 0 && (
            <ul className="chips" aria-label="해석한 조건">
              {res.chips.map((c) => (
                <li key={c.key + c.label}>
                  <span>{c.label}</span>
                  {res.from === 'ai' && (
                    <button aria-label={`${c.label} 조건 빼기`} onClick={() => removeChip(c.key)}>
                      ×
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {res.relaxed.length > 0 && <p className="notice small">결과가 없어 {res.relaxed.join(', ')} 조건을 풀고 찾았어요.</p>}

          {!!res.summary?.length && (
            <div className="summary">
              <AiBadge />
              {res.summary.map((s) => (
                <p key={s}>{s}</p>
              ))}
            </div>
          )}

          {res.hits.length > 0 && (
            <div className="map-layout">
              <div className="list-pane">
                <h2 className="sub">
                  촬영지 {res.total}곳{res.total > res.hits.length && ` 중 ${res.hits.length}곳`}
                </h2>
                <ol className="list">
                  {res.hits.map((h, i) => (
                    <li key={h.location_id}>
                      <article className={`card hit${selected === h.location_id ? ' active' : ''}`}>
                        <span className="rank">{i + 1}</span>
                        <div className="loc-body">
                          <Link to={`/locations/${h.location_id}`}>
                            <strong>{h.name}</strong>
                          </Link>
                          <span className="loc-works">{h.works.slice(0, 3).map((w) => `「${w.title}」`).join(' ')}</span>
                          <span className="meta">
                            {h.gu && <span>{h.gu}</span>}
                            {h.station && h.walk_min != null && (
                              <span>
                                {h.station.endsWith('역') ? h.station : `${h.station}역`} 도보 {h.walk_min}분
                              </span>
                            )}
                          </span>
                          <span className="small muted">
                            출처 {h.source_ids.length}건 · <Link to={`/locations/${h.location_id}`}>출처 카드 보기</Link>
                          </span>
                        </div>
                      </article>
                    </li>
                  ))}
                </ol>
              </div>
              <div className="map-pane">
                <MapView center={centre} markers={markers} onSelect={setSelected} height={320} />
              </div>
            </div>
          )}
          {res.status === 'not_found' && !res.hits.length && (
            <Empty>
              <p>조건에 맞는 촬영지를 찾지 못했어요. 아래 필터로 조건을 바꿔 보세요.</p>
            </Empty>
          )}
        </div>
      )}

      {panel && <FilterPanel key={JSON.stringify(panel)} initial={panel} onSearch={filterSearch} busy={busy} />}
    </section>
  )
}

// ---------- FR-310 필터 패널: AI 없이 장르·구·군·개봉연도로 찾는다
function FilterPanel({ initial, onSearch, busy }: { initial: Filters; onSearch: (f: Filters) => void; busy: boolean }) {
  const [meta, setMeta] = useState<MetaResponse | null>(null)
  const [f, setF] = useState<Filters>(initial) // 미리 채울 값이 바뀌면 key로 새로 만든다
  useEffect(() => {
    api.get<MetaResponse>('/meta').then(setMeta).catch(() => null)
  }, [])
  const toggle = (k: 'genres' | 'districts', v: string) =>
    setF((x) => ({ ...x, [k]: x[k].includes(v) ? x[k].filter((y) => y !== v) : [...x[k], v] }))
  const years = meta?.year_range ? Array.from({ length: meta.year_range.max - meta.year_range.min + 1 }, (_, i) => meta.year_range!.max - i) : []

  return (
    <form
      className="filter-panel"
      aria-label="필터로 찾기"
      onSubmit={(e) => {
        e.preventDefault()
        onSearch(f)
      }}
    >
      <h2 className="sub">필터로 찾기</h2>
      {f.works.length > 0 && (
        <p className="small">
          작품: {f.works.map((w) => w.name).join(', ')}{' '}
          <button type="button" className="link" onClick={() => setF((x) => ({ ...x, works: [] }))}>
            빼기
          </button>
        </p>
      )}
      <fieldset>
        <legend>장르</legend>
        <div className="row wrap gap">
          {(meta?.genres ?? []).map((g) => (
            <label key={g} className={`toggle${f.genres.includes(g) ? ' on' : ''}`}>
              <input type="checkbox" checked={f.genres.includes(g)} onChange={() => toggle('genres', g)} />
              {g}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend>구·군</legend>
        <div className="row wrap gap">
          {(meta?.districts ?? []).map((d) => (
            <label key={d.name} className={`toggle${f.districts.includes(d.name) ? ' on' : ''}`}>
              <input type="checkbox" checked={f.districts.includes(d.name)} onChange={() => toggle('districts', d.name)} />
              {d.name}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="row gap">
        <legend>개봉연도</legend>
        <select aria-label="개봉연도 시작" value={f.year_from ?? ''} onChange={(e) => setF((x) => ({ ...x, year_from: e.target.value ? Number(e.target.value) : null }))}>
          <option value="">처음</option>
          {years.map((y) => (
            <option key={y}>{y}</option>
          ))}
        </select>
        <span aria-hidden>~</span>
        <select aria-label="개봉연도 끝" value={f.year_to ?? ''} onChange={(e) => setF((x) => ({ ...x, year_to: e.target.value ? Number(e.target.value) : null }))}>
          <option value="">끝</option>
          {years.map((y) => (
            <option key={y}>{y}</option>
          ))}
        </select>
      </fieldset>
      <p className="small muted">장르 정보가 있는 작품의 촬영지만 장르로 걸러져요.</p>
      <button className="btn primary" disabled={busy || (!!f.year_from && !!f.year_to && f.year_from > f.year_to)}>
        이 조건으로 찾기
      </button>
    </form>
  )
}
