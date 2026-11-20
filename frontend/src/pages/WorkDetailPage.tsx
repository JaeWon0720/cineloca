// UI-04 작품 상세·AI 소개 (F1, UC-01·02): KMDb 정보(연결된 작품만), AI 작품 소개, 부산 촬영지 목록·지도.
// 포스터·스틸은 보여 주지 않고 KMDb 원문 링크로 대신한다(구현 기준 정정표 11/18, #58).
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router'
import { api, withTimeout } from '../api/client.ts'
import { KMDB_CREDIT, type WorkDetail, type WorkRating } from '../api/works.ts'
import MapView from '../components/MapView.tsx'
import { WorkIntro } from '../components/WorkIntro.tsx'
import { BackLink, Empty, ErrorBox, Loading, Sources } from '../components/common.tsx'
import './works.css'

const NONE = '정보 없음' // FR-103·107: 값이 없는 항목
const RATING_TIMEOUT_MS = 3000 // FR-107: 3초 안에 안 오면 등급 없이 보여 준다

export default function WorkDetailPage() {
  const { id = '' } = useParams()
  const [d, setD] = useState<WorkDetail | null>(null)
  const [ratingOf, setRatingOf] = useState<{ id: string; value: string | null } | null>(null)
  const rating = ratingOf?.id === id ? ratingOf.value : undefined // undefined = 아직 불러오는 중
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api
      .get<WorkDetail>(`/works/${id}`)
      .then((x) => alive && setD(x))
      .catch((e: Error) => alive && setError(e.message))
    withTimeout(api.get<WorkRating>(`/works/${id}/rating`), RATING_TIMEOUT_MS)
      .then((r) => alive && setRatingOf({ id, value: r?.rating ?? null }))
      .catch(() => alive && setRatingOf({ id, value: null }))
    return () => {
      alive = false
    }
  }, [id])

  const pts = useMemo(
    () =>
      (d?.locations ?? [])
        .filter((l) => l.located && l.lat != null && l.lon != null)
        .map((l, i) => ({ id: l.id, lat: l.lat!, lon: l.lon!, label: l.name, order: i + 1 })),
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
        <p className="meta">
          {d.release_year && <span>{d.release_year}</span>}
          {d.genres.length > 0 && <span>{d.genres.join('·')}</span>}
          {!d.kmdb_matched && <span>촬영지 정보 제공 작품</span>}
        </p>
      </header>

      {d.kmdb_matched ? (
        <>
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
            <dd>{rating === undefined ? '불러오는 중' : (rating ?? NONE)}</dd>
          </dl>
          <div className="stack">
            <h2 className="sub">줄거리</h2>
            <p>{d.synopsis ?? NONE}</p>
            <p className="small muted">출처: {KMDB_CREDIT}</p>
          </div>
          <div className="kmdb-links">
            {d.kmdb_url && (
              <a className="btn" href={d.kmdb_url} target="_blank" rel="noreferrer">
                KMDb 원문 보기 (포스터·스틸)
              </a>
            )}
            {d.trailer_url && (
              <a className="btn" href={d.trailer_url} target="_blank" rel="noreferrer">
                예고편 보기
              </a>
            )}
          </div>
        </>
      ) : (
        <p className="notice small">KMDb에 없는 작품이라 작품명과 부산 촬영지만 보여 드려요.</p>
      )}

      <WorkIntro workId={d.id} eligible={d.kmdb_matched} />

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
            </Link>
          </li>
        ))}
      </ul>

      {d.related.length > 0 && (
        <>
          <h2 className="sub">함께 볼 작품</h2>
          <ul className="tags" aria-label="함께 볼 작품">
            {d.related.map((w) => (
              <li key={w.id}>
                <Link to={`/works/${w.id}`}>
                  {w.title}
                  {w.release_year ? ` (${w.release_year})` : ''}
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
      <Sources items={d.sources} />
    </section>
  )
}
