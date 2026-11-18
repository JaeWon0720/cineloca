// UI-04 작품 상세 (F1, UC-01): KMDb 정보(연결된 작품만)와 부산 촬영지 목록·지도
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router'
import { api } from '../api/client.ts'
import type { WorkDetail } from '../api/works.ts'
import MapView from '../components/MapView.tsx'
import { Photo } from '../components/Photo.tsx'
import { BackLink, Empty, ErrorBox, Loading, Sources } from '../components/common.tsx'
import './works.css'

const NONE = '정보 없음' // FR-103·107: 값이 없는 항목

export default function WorkDetailPage() {
  const { id = '' } = useParams()
  const [d, setD] = useState<WorkDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api
      .get<WorkDetail>(`/works/${id}`)
      .then((x) => alive && setD(x))
      .catch((e: Error) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [id])

  const pts = useMemo(
    () =>
      (d?.locations ?? [])
        .filter((l) => l.located && l.lat != null && l.lon != null)
        .map((l) => ({ id: l.id, lat: l.lat!, lon: l.lon!, label: l.name })),
    [d],
  )
  const centre = useMemo<[number, number] | null>(() => (pts[0] ? [pts[0].lat, pts[0].lon] : null), [pts])

  if (error)
    return (
      <section className="page">
        <BackLink to="/works">작품</BackLink>
        <ErrorBox error={error} />
      </section>
    )
  if (!d || String(d.id) !== id) return <Loading />
  return (
    <section className="page">
      <BackLink to="/works">작품</BackLink>
      <header className="work-hero">
        <h1>{d.title}</h1>
        {!d.kmdb_matched && <p className="lead small">촬영지 정보 제공 작품</p>}
      </header>

      {d.kmdb_matched ? (
        <>
          <div className="work-facts">
            <Photo className="poster-img" src={d.poster_url} alt={`${d.title} 포스터`} />
            <dl className="facts">
              <dt>개봉연도</dt>
              <dd>{d.release_year ?? NONE}</dd>
              <dt>장르</dt>
              <dd>{d.genres.length ? d.genres.join('·') : NONE}</dd>
              <dt>감독</dt>
              <dd>{d.director ?? NONE}</dd>
              <dt>출연</dt>
              <dd>{d.cast.length ? d.cast.slice(0, 5).join(', ') : NONE}</dd>
              <dt>관람등급</dt>
              <dd>{d.rating ?? NONE}</dd>
            </dl>
          </div>
          <div className="stack">
            <h2 className="sub">줄거리</h2>
            <p>{d.synopsis ?? NONE}</p>
            {d.kmdb_url && (
              <a className="small" href={d.kmdb_url} target="_blank" rel="noreferrer">
                KMDb 원문 보기
              </a>
            )}
          </div>
          {d.still_urls.length > 0 && (
            <div className="photos" aria-label="스틸">
              {d.still_urls.map((u, i) => (
                <Photo key={u} src={u} alt={`${d.title} 스틸 ${i + 1}`} />
              ))}
            </div>
          )}
          {d.trailer_url && (
            <a className="btn" href={d.trailer_url} target="_blank" rel="noreferrer">
              예고편 보기
            </a>
          )}
        </>
      ) : (
        <p className="notice small">KMDb에 없는 작품이라 작품명과 부산 촬영지만 보여 드립니다.</p>
      )}

      <h2 className="sub">부산 촬영지 {d.locations.length}곳</h2>
      {centre && <MapView center={centre} markers={pts} height={220} />}
      {d.locations.length === 0 && (
        <Empty>
          <p>등록된 부산 촬영지가 없어요.</p>
        </Empty>
      )}
      <ul className="list">
        {d.locations.map((l) => (
          <li key={l.id}>
            <Link className="card place" to={`/locations/${l.id}`}>
              <strong>{l.name}</strong>
              <span className="meta">
                <span>{l.gu ?? '지역 미상'}</span>
                {!l.located && <span>위치 미확인</span>}
              </span>
              {l.scene && <span className="scene">{l.scene}</span>}
            </Link>
          </li>
        ))}
      </ul>
      <Sources items={d.sources} />
    </section>
  )
}
