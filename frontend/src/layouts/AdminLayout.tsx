// 관리자 화면 틀 (UI-10~12 공통, FR-615): 로그인 확인 → 로그인 화면 또는 메뉴 + 관리 화면.
// 비밀번호는 서버(API-020)가 환경변수 해시로 확인하고, 세션은 HttpOnly 쿠키(2시간)로만 오간다.
// 화면은 비밀번호를 저장하지 않고, 로그인 상태도 브라우저 저장소에 두지 않는다(새로 열면 서버에 다시 묻는다).
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, NavLink, Outlet } from 'react-router'
import { ADMIN_MENU, ADMIN_PASSWORD_MAX, type AdminSession } from '../api/admin.ts'
import { API_MOCK, ApiError, api } from '../api/client.ts'
import { ErrorBox, Loading } from '../components/common.tsx'
import { AdminContext, fmtClock, isAdminRequired, type AdminContextValue } from '../lib/admin.ts'
import { MOCK_ADMIN_LOGIN } from '../mocks/adminLogin.ts'
import './admin.css'

type Phase = { kind: 'checking' } | { kind: 'login'; note: string | null } | { kind: 'in'; expiresAt: string }

const EXPIRED_NOTE = '로그인 시간이 지나 다시 로그인해 주세요.'

export function AdminLayout() {
  const [phase, setPhase] = useState<Phase>({ kind: 'checking' })

  // 처음 열 때 쿠키가 아직 유효한지 서버에 묻는다
  useEffect(() => {
    let alive = true
    api
      .get<AdminSession>('/admin/session')
      .then((s) => alive && setPhase({ kind: 'in', expiresAt: s.expires_at }))
      .catch((e) => alive && setPhase({ kind: 'login', note: isAdminRequired(e) ? null : (e as Error).message }))
    return () => {
      alive = false
    }
  }, [])

  const logout = useCallback(async () => {
    await api.del('/admin/session').catch(() => undefined)
    setPhase({ kind: 'login', note: '로그아웃했어요.' })
  }, [])

  const guard = useCallback(<T,>(p: Promise<T>) => {
    return p.catch((e: unknown) => {
      if (isAdminRequired(e)) setPhase({ kind: 'login', note: EXPIRED_NOTE })
      throw e
    })
  }, [])

  const ctx = useMemo<AdminContextValue | null>(
    () => (phase.kind === 'in' ? { expiresAt: phase.expiresAt, logout, guard } : null),
    [phase, logout, guard],
  )

  return (
    <div className="admin-shell">
      <header className="admin-header">
        <Link to="/admin" className="brand">
          씨네로케 <span className="admin-tag">관리자</span>
        </Link>
        {ctx && (
          <div className="admin-session">
            <span className="admin-until">{fmtClock(ctx.expiresAt)}까지 로그인 유지</span>
            <button type="button" className="btn small admin-logout" onClick={() => void logout()}>
              로그아웃
            </button>
          </div>
        )}
      </header>
      {API_MOCK && (
        <p className="mock-banner" role="note">
          목업으로 동작 중이에요. 목업 관리자 비밀번호는 <code>{MOCK_ADMIN_LOGIN}</code>예요(실제 서버와 무관).
        </p>
      )}
      {phase.kind === 'checking' && (
        <main className="admin-main">
          <Loading rows={2} />
        </main>
      )}
      {phase.kind === 'login' && (
        <main className="admin-main">
          <LoginForm note={phase.note} onDone={(s) => setPhase({ kind: 'in', expiresAt: s.expires_at })} />
        </main>
      )}
      {ctx && (
        <AdminContext.Provider value={ctx}>
          <div className="admin-body">
            <nav className="admin-nav" aria-label="관리 메뉴">
              <ul>
                {ADMIN_MENU.map((m) => (
                  <li key={m.to}>
                    <NavLink to={m.to} className={({ isActive }) => `admin-nav-link${isActive ? ' active' : ''}`}>
                      <span>{m.label}</span>
                      <small>{m.screen}</small>
                    </NavLink>
                  </li>
                ))}
              </ul>
              <Link to="/" className="admin-nav-back">
                사용자 화면으로
              </Link>
            </nav>
            <main className="admin-main">
              <Outlet />
            </main>
          </div>
        </AdminContext.Provider>
      )}
    </div>
  )
}

function LoginForm({ note, onDone }: { note: string | null; onDone: (s: AdminSession) => void }) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!password || busy) return
    setBusy(true)
    setError(null)
    try {
      const s = await api.post<AdminSession>('/admin/session', { password })
      onDone(s)
    } catch (err) {
      // 비밀번호 칸은 비워서 틀린 값을 다시 보내지 않게 한다
      setPassword('')
      setError(err instanceof ApiError ? err.message : '로그인하지 못했어요. 잠시 후 다시 시도해 주세요.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="admin-login">
      <h1>관리자 로그인</h1>
      <p className="page-desc">동기화 리포트·검수 큐·추천 코스 관리는 관리자만 쓸 수 있어요.</p>
      {note && (
        <p className="notice" role="status">
          {note}
        </p>
      )}
      <form onSubmit={submit} className="admin-login-form">
        <label className="field">
          <span>관리자 비밀번호</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            maxLength={ADMIN_PASSWORD_MAX}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoFocus
          />
        </label>
        <button type="submit" className="btn primary" disabled={busy || !password}>
          {busy ? '확인 중…' : '로그인'}
        </button>
        <ErrorBox error={error} />
      </form>
      <p className="page-meta">5회 연속 틀리면 10분 동안 로그인할 수 없어요. 로그인은 2시간 동안 유지돼요.</p>
    </section>
  )
}
