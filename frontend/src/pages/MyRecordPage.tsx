// UI-08 내 기록 (F5, UC-11·12): 스탬프북·완주 배지(FR-507·508)와 저장한 코스(FR-406).
// 스탬프·배지 토큰과 코스는 이 브라우저에만 있다(FR-509). 서명이 맞지 않는 토큰은 서버가 걸러 주고 화면에 보이지 않는다.
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { api } from '../api/client.ts'
import type { VerifyResponse } from '../api/stamps.ts'
import { Empty, ErrorBox, Loading } from '../components/common.tsx'
import { MAX_SAVED_COURSES, savedCourses, tokens, type SavedCourse } from '../lib/wallet.ts'
import './courses.css'

// FR-509 저장 위치 고지
const STORAGE_NOTICE = '스탬프는 이 브라우저에만 저장되며, 기기를 바꾸거나 브라우저 데이터를 지우면 사라집니다.'

const fmtDate = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function MyRecordPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'courses' ? 'courses' : 'stamps'
  return (
    <section className="page">
      <header className="page-head">
        <h1>내 기록</h1>
      </header>
      <div className="seg" role="tablist" aria-label="기록 종류">
        <button role="tab" aria-selected={tab === 'stamps'} className={tab === 'stamps' ? 'on' : ''} onClick={() => setParams({})}>
          스탬프북
        </button>
        <button role="tab" aria-selected={tab === 'courses'} className={tab === 'courses' ? 'on' : ''} onClick={() => setParams({ tab: 'courses' })}>
          저장한 코스
        </button>
      </div>
      {tab === 'stamps' ? <StampBook /> : <SavedCourses />}
    </section>
  )
}

function StampBook() {
  const [held] = useState(() => tokens.all())
  // 토큰이 없으면 서버에 물을 것도 없다
  const [res, setRes] = useState<VerifyResponse | null>(() => (held.length ? null : { valid: [], badges: [], invalid_count: 0, unavailable_count: 0 }))
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!held.length) return
    let alive = true
    api
      .post<VerifyResponse>('/stamps/verify', { tokens: held })
      .then((r) => alive && setRes(r))
      .catch((e: Error) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [held])

  return (
    <div className="stack">
      <p className="notice small" role="note">
        {STORAGE_NOTICE}
      </p>
      <ErrorBox error={error} />
      {!res && !error && <Loading rows={3} />}
      {res && (
        <>
          {res.invalid_count > 0 && <p className="small muted">확인되지 않은 기록 {res.invalid_count}개는 보여 주지 않아요.</p>}
          {res.unavailable_count > 0 && <p className="small muted">지금은 확인할 수 없는 기록 {res.unavailable_count}개가 있어요. 잠시 후 다시 열어 주세요.</p>}

          <h2 className="sub">완주 배지 {res.badges.length}개</h2>
          {res.badges.length === 0 ? (
            <p className="small muted">코스의 인증 대상 장소를 모두 인증하면 배지를 받아요.</p>
          ) : (
            <ul className="badges">
              {res.badges.map((b) => (
                <li key={b.course_key + b.issued_at} className="badge">
                  <strong>{b.course_title ?? '저장한 코스 완주'}</strong>
                  <span className="small">{fmtDate(b.issued_at)}</span>
                </li>
              ))}
            </ul>
          )}

          <h2 className="sub">스탬프 {res.valid.length}개</h2>
          {res.valid.length === 0 ? (
            <Empty>
              <p>{held.length ? '보여 줄 수 있는 스탬프가 없어요.' : '아직 받은 스탬프가 없어요.'}</p>
              <p className="small">촬영지 상세나 코스에서 ‘방문 인증’을 누르면 여기에 모여요.</p>
              <Link className="btn small" to="/">
                지도에서 촬영지 찾기
              </Link>
            </Empty>
          ) : (
            <ul className="stamp-grid">
              {[...res.valid]
                .sort((a, z) => z.certified_at.localeCompare(a.certified_at))
                .map((s) => (
                  <li key={s.location.id} className="stamp-card">
                    <Link to={`/locations/${s.location.id}`}>
                      <strong>{s.location.name}</strong>
                    </Link>
                    <span className="small">
                      {s.works.map((w) => `「${w.title}」`).join(' ')}
                      {s.work_count > s.works.length && ` 외 ${s.work_count - s.works.length}편`}
                    </span>
                    <span className="small muted">{fmtDate(s.certified_at)} 인증</span>
                  </li>
                ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

function SavedCourses() {
  const [list, setList] = useState<SavedCourse[]>(() => savedCourses.all())
  const [confirm, setConfirm] = useState<string | null>(null)

  return (
    <div className="stack">
      <p className="small muted">
        코스는 이 브라우저에 {MAX_SAVED_COURSES}개까지 저장돼요({list.length}/{MAX_SAVED_COURSES}). 서버에는 저장하지 않아요.
      </p>
      {list.length === 0 && (
        <Empty>
          <p>저장한 코스가 없어요.</p>
          <Link className="btn small" to="/courses?tab=my">
            나만의 코스 만들기
          </Link>
        </Empty>
      )}
      <ul className="list">
        {list.map((c) => (
          <li key={c.key} className="card saved">
            <Link to={`/courses/saved/${c.key}`}>
              <strong>{c.title}</strong>
            </Link>
            <span className="meta">
              <span>{c.start_label} 출발</span>
              <span>{c.location_ids.length}곳</span>
              <span>{fmtDate(c.saved_at)} 저장</span>
            </span>
            {confirm === c.key ? (
              <div className="row gap">
                <span className="small">지울까요?</span>
                <button
                  className="btn small"
                  onClick={() => {
                    savedCourses.remove(c.key)
                    setList(savedCourses.all())
                    setConfirm(null)
                  }}
                >
                  지우기
                </button>
                <button className="btn small" onClick={() => setConfirm(null)}>
                  그대로 두기
                </button>
              </div>
            ) : (
              <button className="link small" onClick={() => setConfirm(c.key)}>
                삭제
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
