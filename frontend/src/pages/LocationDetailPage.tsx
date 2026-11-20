// UI-02 촬영지 상세 (F2, UC-04): 주소·가까운 역·촬영 작품·주의 안내·길찾기 (FR-207~210), 방문 인증 (F5, FR-501~506)
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router'
import { api } from '../api/client.ts'
import type { Caution, LocationDetail } from '../api/locations.ts'
import type { VerifyResponse } from '../api/stamps.ts'
import { StampButton } from '../components/StampButton.tsx'
import { tokens } from '../lib/wallet.ts'
import { Photo } from '../components/Photo.tsx'
import MapView from '../components/MapView.tsx'
import { BackLink, ErrorBox, Loading, Sources } from '../components/common.tsx'
import { stationLabel } from '../lib/geo.ts'
import './map.css'

// FR-209: 사유지·주거지·학교는 '외부에서만 관람' 안내를 항상 보여 준다
const CAUTION: Record<Caution, string> = {
  private: '사유지·영업 시설이에요. 외부에서만 관람해 주세요.',
  residential: '주민이 사는 곳이에요. 외부에서만 관람해 주세요.',
  school: '학교예요. 외부에서만 관람해 주세요.',
}


export default function LocationDetailPage() {
  const { id = '' } = useParams()
  const [d, setD] = useState<LocationDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [doneId, setDoneId] = useState<string | null>(null) // 인증한 촬영지(다른 촬영지로 옮기면 자연히 false)
  const done = doneId === id

  useEffect(() => {
    let alive = true
    api
      .get<LocationDetail>(`/locations/${id}`)
      .then((x) => alive && setD(x))
      .catch((e: Error) => alive && setError(e.message))
    // 이미 인증했는지는 브라우저에 있는 토큰을 서버가 확인해 알려 준다(API-017)
    const held = tokens.all()
    if (held.length)
      api
        .post<VerifyResponse>('/stamps/verify', { tokens: held })
        .then((v) => alive && v.valid.some((x) => String(x.location.id) === id) && setDoneId(id))
        .catch(() => null)
    return () => {
      alive = false
    }
  }, [id])

  const centre = useMemo<[number, number] | null>(() => (d?.lat != null && d.lon != null ? [d.lat, d.lon] : null), [d])
  const markers = useMemo(() => (d && centre ? [{ id: d.id, lat: centre[0], lon: centre[1], label: d.name, active: true }] : []), [d, centre])

  if (error)
    return (
      <section className="page">
        <BackLink to="/">지도</BackLink>
        <ErrorBox error={error} />
      </section>
    )
  if (!d || String(d.id) !== id) return <Loading />
  const st = d.nearest_station
  return (
    <section className="page">
      <BackLink to="/">지도</BackLink>
      <header className="loc-hero">
        <h1>{d.name}</h1>
        <p>{d.address ?? '주소 미상'}</p>
        <p className="meta">
          {d.gu && <span>{d.gu}</span>}
          {st && st.walk_min <= 30 && (
            <span>
              {stationLabel(st.name)}({st.line}) 도보 {Math.round(st.walk_min)}분
            </span>
          )}
        </p>
      </header>

      {d.caution && (
        <p className="caution" role="note">
          <span aria-hidden>⚠</span>
          <span>
            {CAUTION[d.caution]}
            {d.caution_notice && d.caution_notice !== CAUTION[d.caution] && ` ${d.caution_notice}`}
          </span>
        </p>
      )}
      {d.safety_note && (
        <p className="caution" role="note">
          <span aria-hidden>⚠</span>
          <span>{d.safety_note}</span>
        </p>
      )}

      <div className="photos">
        {d.photos.length ? d.photos.map((p) => <Photo key={p.url} src={p.url} alt={p.title ?? d.name} />) : <Photo alt={`${d.name} 사진 없음`} />}
      </div>

      {centre ? (
        <MapView center={centre} markers={markers} height={220} />
      ) : (
        <p className="notice">위치 미확인 — 좌표를 확인하지 못해 지도에 표시하지 않습니다.</p>
      )}

      {d.directions_url && (
        <a className="btn primary" href={d.directions_url} target="_blank" rel="noreferrer">
          카카오맵 길찾기
        </a>
      )}
      <StampButton locationId={d.id} cert={d.cert} done={done} onStamped={() => setDoneId(id)} />

      <h2 className="sub">이곳에서 찍은 작품 {d.works.length}편</h2>
      <ul className="list">
        {d.works.map((w) => (
          <li key={w.id}>
            <Link className="card" to={`/works/${w.id}`}>
              <span className="work-row">
                <strong>「{w.title}」</strong>
                {w.release_year && <span className="year">{w.release_year}</span>}
              </span>
              {!w.kmdb_matched && <span className="small muted">촬영지 정보 제공 작품</span>}
            </Link>
          </li>
        ))}
      </ul>
      {d.tags.length > 0 && (
        <ul className="tags" aria-label="장소 유형">
          {d.tags.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      )}
      <Sources items={d.sources} />
    </section>
  )
}
