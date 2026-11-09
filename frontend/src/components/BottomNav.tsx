import { NavLink } from 'react-router'

// SRS 6.1: 사용자 화면은 하단 탭 5개로 이동한다.
const TABS = [
  { to: '/', label: '지도', end: true },
  { to: '/works', label: '작품', end: false },
  { to: '/ask', label: 'AI 탐색', end: false },
  { to: '/courses', label: '코스', end: false },
  { to: '/me', label: '내 기록', end: false },
] as const

export function BottomNav() {
  return (
    <nav className="bottom-nav" aria-label="주요 메뉴">
      <ul>
        {TABS.map((tab) => (
          <li key={tab.to}>
            <NavLink
              to={tab.to}
              end={tab.end}
              className={({ isActive }) => (isActive ? 'tab active' : 'tab')}
            >
              {tab.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}
