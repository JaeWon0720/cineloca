import { Link, Outlet } from 'react-router'
import { BottomNav } from '../components/BottomNav.tsx'

export function UserLayout() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <Link to="/" className="brand">
          씨네로케
        </Link>
        <Link to="/sources" className="header-link">
          데이터 출처
        </Link>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
      <BottomNav />
    </div>
  )
}
