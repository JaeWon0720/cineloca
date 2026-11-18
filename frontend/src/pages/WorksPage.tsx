// UI-03 작품 목록 (F1, UC-01): 작품명·배우·감독 검색, 장르 필터, 정렬
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { api } from '../api/client.ts'
import { WORKS_PAGE_SIZE, type WorkListResponse } from '../api/works.ts'
import { Empty, ErrorBox, Loading, Sources } from '../components/common.tsx'
import './works.css'

export default function WorksPage() {
  const [q, setQ] = useState('')
  const [genre, setGenre] = useState('')
  const [sort, setSort] = useState('title')
  const [page, setPage] = useState(1)
  const [res, setRes] = useState<WorkListResponse | null>(null)
  const [genres, setGenres] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const t = setTimeout(() => {
      const p = new URLSearchParams({ sort, page: String(page), size: String(WORKS_PAGE_SIZE) })
      if (q.trim()) p.set('q', q.trim())
      if (genre) p.set('genre', genre)
      api
        .get<WorkListResponse>(`/works?${p}`)
        .then((r) => {
          if (!alive) return
          setRes(r)
          setError(null)
          // 장르 선택지는 필터를 걸지 않은 첫 목록에서 모은다
          setGenres((g) => (g.length ? g : [...new Set(r.items.flatMap((w) => w.genres))].sort((a, z) => a.localeCompare(z, 'ko'))))
        })
        .catch((e: Error) => alive && setError(e.message))
    }, 200)
    return () => {
      alive = false
      clearTimeout(t)
    }
  }, [q, genre, sort, page])

  const items = useMemo(() => res?.items ?? [], [res])
  return (
    <section className="page page-works">
      <header className="page-head">
        <h1>작품</h1>
        <p className="lead small">부산에서 촬영한 영화·드라마{res ? ` ${res.total}편` : ''}</p>
      </header>
      <div className="works-controls">
        <input
          type="search"
          className="search"
          placeholder="작품명으로 찾기"
          aria-label="작품 검색"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setPage(1)
          }}
          maxLength={50}
        />
        <select
          value={genre}
          onChange={(e) => {
            setGenre(e.target.value)
            setPage(1)
          }}
          aria-label="장르"
        >
          <option value="">장르 전체</option>
          {genres.map((g) => (
            <option key={g}>{g}</option>
          ))}
        </select>
        <select
          value={sort}
          onChange={(e) => {
            setSort(e.target.value)
            setPage(1)
          }}
          aria-label="정렬"
        >
          <option value="title">이름순</option>
          <option value="year">최신순</option>
          <option value="locations">촬영지 많은 순</option>
        </select>
      </div>
      <ErrorBox error={error} />
      {!res && !error && <Loading rows={4} />}
      {res?.total === 0 && (
        <Empty>
          <p>검색 결과가 없어요.</p>
          <p className="small">작품명을 줄여서 입력하거나 장르를 ‘전체’로 바꿔 보세요.</p>
        </Empty>
      )}
      <ul className="work-grid">
        {items.map((w) => (
          <li key={w.id}>
            <Link to={`/works/${w.id}`} className="work-card">
              <span className="poster" aria-hidden>
                <span>{w.title}</span>
              </span>
              <span className="work-info-line">
                <strong>{w.title}</strong>
                <span className="meta">
                  {w.release_year && <span>{w.release_year}</span>}
                  {w.genres.length > 0 && <span>{w.genres.join('·')}</span>}
                </span>
                <span className="loc-count">부산 촬영지 {w.location_count}곳</span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {res && res.total > res.size && (
        <nav className="pager" aria-label="작품 목록 쪽">
          <button className="btn small" disabled={res.page <= 1} onClick={() => setPage(res.page - 1)}>
            이전
          </button>
          <span className="small muted">
            {res.page} / {Math.ceil(res.total / res.size)}쪽
          </span>
          <button className="btn small" disabled={res.page * res.size >= res.total} onClick={() => setPage(res.page + 1)}>
            다음
          </button>
        </nav>
      )}
      <Sources items={res?.sources} />
    </section>
  )
}
