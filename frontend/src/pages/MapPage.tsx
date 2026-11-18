// UI-01 지도 홈 (F2, UC-03): 기준 위치 반경 안 촬영지를 지도 마커와 거리순 목록으로 보여 준다.
// 기준 위치는 현재 위치(못 얻으면 부산역, FR-201)이고, 구·군이나 역을 직접 고를 수도 있다(FR-202).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { api } from '../api/client.ts'
import type { LocationSearchRequest, LocationSearchResponse, MetaResponse, NamedPoint, RadiusM } from '../api/locations.ts'
import { LocationCard, WorksLine } from '../components/LocationCard.tsx'
import MapView from '../components/MapView.tsx'
import { ErrorBox, Loading, Sources } from '../components/common.tsx'
import { fmtDist, getPosition, stationLabel } from '../lib/geo.ts'
import './map.css'

const radiusLabel = (r: number) => (r >= 1000 ? `${r / 1000}km` : `${r}m`)
// KMDb와 연결되지 않은 작품은 장르 정보가 없어서 장르 필터에 걸리지 않는다(백엔드 PR #30).
const GENRE_HINT = '장르 정보가 있는 작품의 촬영지만 보여 줍니다.'

export default function MapPage() {
  const [meta, setMeta] = useState<MetaResponse | null>(null)
  const [here, setHere] = useState<NamedPoint | null>(null)
  const [hereMsg, setHereMsg] = useState<string | null>(null)
  const [pick, setPick] = useState('here') // 'here' | 'gu:<이름>' | 'st:<역 ID>'
  const [radius, setRadius] = useState<RadiusM | null>(null)
  const [genre, setGenre] = useState('')
  const [yearFrom, setYearFrom] = useState('')
  const [yearTo, setYearTo] = useState('')
  const [gu, setGu] = useState('')
  const [showFilters, setShowFilters] = useState(false)
  const [res, setRes] = useState<LocationSearchResponse | null>(null)
  const [sel, setSel] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<MetaResponse>('/meta')
      .then(setMeta)
      .catch((e: Error) => setError(e.message))
    getPosition()
      .then((f) => setHere({ label: '현재 위치', lat: f.lat, lon: f.lon }))
      .catch((e: Error) => setHereMsg(e.message))
  }, [])

  // 기준 위치: 직접 고른 구·군이나 역 > 현재 위치 > 부산역
  const base = useMemo<NamedPoint | null>(() => {
    if (!meta) return null
    if (pick.startsWith('gu:')) {
      const d = meta.districts.find((x) => x.name === pick.slice(3))
      if (d) return { label: d.name, lat: d.lat, lon: d.lon }
    }
    if (pick.startsWith('st:')) {
      const s = meta.stations.find((x) => x.id === pick.slice(3))
      if (s) return { label: stationLabel(s.name), lat: s.lat, lon: s.lon }
    }
    return here ?? meta.default_base
  }, [meta, pick, here])
  const r = radius ?? meta?.default_radius_m ?? 1000

  useEffect(() => {
    if (!base) return
    let alive = true
    const body: LocationSearchRequest = {
      base: { lat: base.lat, lon: base.lon },
      radius_m: r,
      genres: genre ? [genre] : [],
      year_from: yearFrom ? Number(yearFrom) : null,
      year_to: yearTo ? Number(yearTo) : null,
      gu: gu ? [gu] : [],
    }
    api
      .post<LocationSearchResponse>('/locations/search', body)
      .then((x) => {
        if (!alive) return
        setRes(x)
        setSel(null)
        setError(null)
      })
      .catch((e: Error) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [base, r, genre, yearFrom, yearTo, gu])

  const markers = useMemo(
    () => (res?.items ?? []).map((l, i) => ({ id: l.id, lat: l.lat, lon: l.lon, label: l.name, active: l.id === sel, order: i + 1 })),
    [res, sel],
  )
  const centre = useMemo<[number, number] | null>(() => (base ? [base.lat, base.lon] : null), [base])
  const me = useMemo<[number, number] | null>(() => (here ? [here.lat, here.lon] : null), [here])
  const onSelect = useCallback((id: number) => setSel(id), [])
  const selected = res?.items.find((l) => l.id === sel)
  const years = useMemo(() => {
    const yr = meta?.year_range
    return yr ? Array.from({ length: yr.max - yr.min + 1 }, (_, i) => yr.max - i) : []
  }, [meta])
  const wider = meta?.radius_options.find((x) => x > r)
  const filterCount = (genre ? 1 : 0) + (yearFrom || yearTo ? 1 : 0) + (gu ? 1 : 0)
  const clearFilters = () => {
    setGenre('')
    setYearFrom('')
    setYearTo('')
    setGu('')
  }

  return (
    <section className="page page-wide">
      <header className="page-head">
        <h1>지도</h1>
        <p className="lead small">
          {base ? `${base.label} 기준 ${radiusLabel(r)} 안 촬영지를 가까운 순으로 보여 드립니다.` : '기준 위치를 확인하는 중…'}
          {pick === 'here' && !here && hereMsg && ` ${hereMsg}`}
        </p>
      </header>

      <div className="map-controls">
        <label className="base-pick">
          <span>기준</span>
          <select value={pick} onChange={(e) => setPick(e.target.value)} aria-label="기준 위치">
            <option value="here">{here ? '현재 위치' : `${meta?.default_base.label ?? '부산역'} (기본)`}</option>
            <optgroup label="구·군">
              {meta?.districts.map((d) => (
                <option key={d.name} value={`gu:${d.name}`}>
                  {d.name}
                </option>
              ))}
            </optgroup>
            {!!meta?.stations.length && (
              <optgroup label="도시철도역">
                {meta.stations.map((s) => (
                  <option key={s.id} value={`st:${s.id}`}>
                    {stationLabel(s.name)} ({s.line})
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        <div className="seg" role="group" aria-label="반경">
          {(meta?.radius_options ?? []).map((x) => (
            <button key={x} className={x === r ? 'on' : ''} aria-pressed={x === r} onClick={() => setRadius(x)}>
              {radiusLabel(x)}
            </button>
          ))}
        </div>
        <button
          className={`btn small filter-toggle${filterCount ? ' has' : ''}`}
          aria-expanded={showFilters}
          aria-controls="map-filters"
          onClick={() => setShowFilters((v) => !v)}
        >
          필터{filterCount > 0 && <span className="count">{filterCount}</span>}
        </button>
      </div>

      {showFilters && (
        <div id="map-filters" className="filter-sheet">
          <label className="field">
            <span>장르</span>
            <select value={genre} onChange={(e) => setGenre(e.target.value)} aria-label="장르">
              <option value="">전체</option>
              {meta?.genres.map((g) => <option key={g}>{g}</option>)}
            </select>
            <small className="hint">{GENRE_HINT}</small>
          </label>
          <fieldset className="field year-range">
            <legend>개봉연도</legend>
            <span className="row gap">
              <select value={yearFrom} onChange={(e) => setYearFrom(e.target.value)} aria-label="개봉연도 시작">
                <option value="">처음</option>
                {years
                  .filter((y) => !yearTo || y <= Number(yearTo))
                  .map((y) => (
                    <option key={y}>{y}</option>
                  ))}
              </select>
              <span aria-hidden>~</span>
              <select value={yearTo} onChange={(e) => setYearTo(e.target.value)} aria-label="개봉연도 끝">
                <option value="">끝</option>
                {years
                  .filter((y) => !yearFrom || y >= Number(yearFrom))
                  .map((y) => (
                    <option key={y}>{y}</option>
                  ))}
              </select>
            </span>
          </fieldset>
          <label className="field">
            <span>구·군</span>
            <select value={gu} onChange={(e) => setGu(e.target.value)} aria-label="구·군 필터">
              <option value="">전체</option>
              {meta?.districts.map((d) => <option key={d.name}>{d.name}</option>)}
            </select>
          </label>
          {filterCount > 0 && (
            <button className="link clear" onClick={clearFilters}>
              필터 지우기
            </button>
          )}
        </div>
      )}

      <div className="map-layout">
        <div className="map-pane">
          {centre ? <MapView center={centre} markers={markers} radiusM={r} me={me} onSelect={onSelect} height={360} /> : <Loading rows={1} />}

          {selected && (
            <article className="ticket" aria-label={`${selected.name} 요약`}>
              <div className="ticket-main">
                <h2>{selected.name}</h2>
                <p className="loc-works">
                  <WorksLine loc={selected} />
                </p>
                <p className="meta">
                  {selected.gu && <span>{selected.gu}</span>}
                  {selected.nearest_station && selected.nearest_station.walk_min <= 30 && (
                    <span>
                      {stationLabel(selected.nearest_station.name)} 도보 {Math.round(selected.nearest_station.walk_min)}분
                    </span>
                  )}
                </p>
              </div>
              <div className="ticket-stub">
                <strong>{fmtDist(selected.distance_m)}</strong>
                <span>기준에서</span>
              </div>
              <div className="ticket-actions">
                <Link className="btn primary" to={`/locations/${selected.id}`}>
                  자세히 보기
                </Link>
                <a className="btn" href={selected.directions_url} target="_blank" rel="noreferrer">
                  카카오맵 길찾기
                </a>
              </div>
            </article>
          )}
        </div>

        <div className="list-pane">
          <ErrorBox error={error} />
          <h2 className="sub">반경 안 촬영지 {res?.total ?? 0}곳</h2>
          {res && res.items.length < res.total && (
            <p className="hint">가까운 {res.items.length}곳만 보여 줍니다. 반경을 줄이거나 필터를 걸면 더 좁혀 볼 수 있어요.</p>
          )}
          {genre && <p className="hint">장르 ‘{genre}’ 적용 중: {GENRE_HINT}</p>}
          {!res && !error && <Loading />}
          {res && res.total === 0 && (
            <div className="empty">
              <p>이 반경에는 조건에 맞는 촬영지가 없어요.</p>
              {wider && (
                <button className="btn small" onClick={() => setRadius(wider)}>
                  반경을 {radiusLabel(wider)}로 넓히기
                </button>
              )}
              {genre && (
                <button className="btn small" onClick={() => setGenre('')}>
                  장르 조건 빼고 보기
                </button>
              )}
            </div>
          )}
          <ol className="list">
            {res?.items.map((l, i) => (
              <li key={l.id}>
                <LocationCard loc={l} rank={i + 1} onClick={() => setSel(l.id)} active={l.id === sel} />
              </li>
            ))}
          </ol>
          <Sources items={res?.sources} />
        </div>
      </div>
    </section>
  )
}
