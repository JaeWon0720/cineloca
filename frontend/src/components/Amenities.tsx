// F4 코스 주변 시설 추천 (API-012, FR-414~418): 밥·카페·숙소를 구간마다 2~3곳 골라 준다.
// 원하는 조건은 말로 쓰거나(AI가 구조화, FR-415) 직접 고른다. 추천 이유가 AI 문장이면 'AI 생성'을 붙인다(FR-707).
// 조회 실패('시설 정보를 불러오지 못함')와 0건('근처 시설 없음')은 서버가 구간 notice로 구분해 준다.
import { useState, type FormEvent } from 'react'
import { api } from '../api/client.ts'
import {
  AMENITY_TEXT_MAX,
  FOOD,
  LODGING,
  type AmenityResponse,
  type FacilityKind,
  type FoodCategory,
  type LodgingCategory,
} from '../api/courses.ts'
import { fmtDist } from '../lib/geo.ts'
import { useNotice } from '../lib/notice.ts'
import { aiErrorMessage } from '../lib/ai.ts'
import { AiBadge } from './ai.tsx'
import { ErrorBox, Loading, Sources } from './common.tsx'

const KIND_LABEL: Record<FacilityKind, string> = { food: '밥·카페', lodging: '숙소' }

export function Amenities({ locationIds }: { locationIds: number[] }) {
  const { show } = useNotice()
  const [mode, setMode] = useState<'text' | 'pick'>('pick')
  const [text, setText] = useState('')
  const [kinds, setKinds] = useState<FacilityKind[]>(['food'])
  const [food, setFood] = useState<FoodCategory[]>([])
  const [lodging, setLodging] = useState<LodgingCategory[]>([])
  const [res, setRes] = useState<AmenityResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const tooLong = text.length > AMENITY_TEXT_MAX

  if (locationIds.length < 2) return null

  const flip = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (tooLong || busy) return
    setBusy(true)
    setError(null)
    try {
      const body =
        mode === 'text' && text.trim()
          ? { location_ids: locationIds, text: text.trim() }
          : { location_ids: locationIds, selected: { kinds, food: kinds.includes('food') ? food : [], lodging: kinds.includes('lodging') ? lodging : [] } }
      setRes(await api.post<AmenityResponse>('/courses/amenities', body))
    } catch (err) {
      setRes(null)
      setError(aiErrorMessage(err, show))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="amenities" aria-labelledby="amenity-title">
      <h2 id="amenity-title" className="sub">
        코스 주변 밥·카페·숙소
      </h2>
      <form className="stack" onSubmit={submit}>
        <div className="seg" role="tablist" aria-label="조건 입력 방법">
          <button type="button" role="tab" aria-selected={mode === 'pick'} className={mode === 'pick' ? 'on' : ''} onClick={() => setMode('pick')}>
            직접 고르기
          </button>
          <button type="button" role="tab" aria-selected={mode === 'text'} className={mode === 'text' ? 'on' : ''} onClick={() => setMode('text')}>
            말로 쓰기
          </button>
        </div>
        {mode === 'text' ? (
          <div className="stack">
            <label htmlFor="amenity-text" className="small muted">
              원하는 조건(선택)
            </label>
            <input
              id="amenity-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="예: 중간에 카페, 마지막엔 바다 보이는 숙소"
              aria-invalid={tooLong}
            />
            <span className={tooLong ? 'count over' : 'count'}>
              {text.length}/{AMENITY_TEXT_MAX}
            </span>
          </div>
        ) : (
          <div className="stack">
            <div className="row wrap gap" role="group" aria-label="종류">
              {(Object.keys(KIND_LABEL) as FacilityKind[]).map((k) => (
                <label key={k} className={`toggle${kinds.includes(k) ? ' on' : ''}`}>
                  <input type="checkbox" checked={kinds.includes(k)} onChange={() => setKinds((x) => flip(x, k))} />
                  {KIND_LABEL[k]}
                </label>
              ))}
            </div>
            {kinds.includes('food') && (
              <div className="row wrap gap" role="group" aria-label="밥·카페 분류">
                {FOOD.map((c) => (
                  <label key={c} className={`toggle${food.includes(c) ? ' on' : ''}`}>
                    <input type="checkbox" checked={food.includes(c)} onChange={() => setFood((x) => flip(x, c))} />
                    {c}
                  </label>
                ))}
              </div>
            )}
            {kinds.includes('lodging') && (
              <div className="row wrap gap" role="group" aria-label="숙소 분류">
                {LODGING.map((c) => (
                  <label key={c} className={`toggle${lodging.includes(c) ? ' on' : ''}`}>
                    <input type="checkbox" checked={lodging.includes(c)} onChange={() => setLodging((x) => flip(x, c))} />
                    {c}
                  </label>
                ))}
              </div>
            )}
          </div>
        )}
        <button className="btn" disabled={busy || tooLong || (mode === 'pick' && kinds.length === 0)}>
          주변 시설 찾기
        </button>
      </form>

      {busy && <Loading rows={2} />}
      <ErrorBox error={error} />
      {res && !busy && (
        <div className="stack">
          {res.conditions.status === 'ai' && (
            <p className="small muted">
              AI가 읽은 조건: {[...res.conditions.food, ...res.conditions.lodging, ...res.conditions.keywords].join(', ') || '밥·카페'}
            </p>
          )}
          {res.notices.map((n) => (
            <p key={n} className="notice small" role="note">
              {n}
            </p>
          ))}
          {res.segments.map((s) => (
            <div key={s.key} className="segment">
              <h3>
                {KIND_LABEL[s.kind]} · {s.anchor}
              </h3>
              {s.notice && <p className="small muted">{s.notice}</p>}
              <ul className="list">
                {s.picks.map((p) => (
                  <li key={p.content_id} className="card pick">
                    <div className="loc-top">
                      <strong>{p.name}</strong>
                      <span className="dist">{fmtDist(p.distance_m)}</span>
                    </div>
                    <span className="meta">
                      {p.category && <span>{p.category}</span>}
                      {p.address && <span>{p.address}</span>}
                    </span>
                    <p className="reason">
                      {p.ai_generated && <AiBadge />} {p.reason}
                    </p>
                    <a className="small" href={p.directions_url} target="_blank" rel="noreferrer">
                      카카오맵 길찾기
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <Sources items={res.sources} />
        </div>
      )}
    </section>
  )
}
