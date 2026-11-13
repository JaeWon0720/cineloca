import { Link, Outlet } from 'react-router'
import { API_MOCK } from '../api/client.ts'
import { BottomNav } from '../components/BottomNav.tsx'
import { NoticeProvider } from '../components/NoticeGate.tsx'

export function UserLayout() {
  return (
    <NoticeProvider>
      <div className="app-shell">
        <header className="app-header">
          <Link to="/" className="brand">
            씨네로케
          </Link>
          <Link to="/sources" className="header-link">
            데이터 출처
          </Link>
        </header>
        {API_MOCK && (
          <p className="mock-banner" role="note">
            목업 데이터로 동작 중이에요(백엔드 연결 전). 표시되는 내용은 화면 개발용 예시입니다.
          </p>
        )}
        <main className="app-main">
          <Outlet />
        </main>
        <BottomNav />
      </div>
    </NoticeProvider>
  )
}
