// COMP-01 Web Client 라우팅 (SRS 6.1 화면 목록)
import { Route, Routes } from 'react-router'
import './App.css'
import { UserLayout } from './layouts/UserLayout.tsx'
import AdminPage from './pages/AdminPage.tsx'
import AskPage from './pages/AskPage.tsx'
import CoursesPage from './pages/CoursesPage.tsx'
import MapPage from './pages/MapPage.tsx'
import MyRecordPage from './pages/MyRecordPage.tsx'
import NotFoundPage from './pages/NotFoundPage.tsx'
import SourcesPage from './pages/SourcesPage.tsx'
import WorksPage from './pages/WorksPage.tsx'

export default function App() {
  return (
    <Routes>
      {/* 사용자 화면: 상단 헤더 + 하단 탭 5개 */}
      <Route element={<UserLayout />}>
        <Route index element={<MapPage />} />
        <Route path="works" element={<WorksPage />} />
        <Route path="ask" element={<AskPage />} />
        <Route path="courses" element={<CoursesPage />} />
        <Route path="me" element={<MyRecordPage />} />
        <Route path="sources" element={<SourcesPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      {/* 관리자 화면: 별도 주소, 하단 탭 없음 (SRS 6.1) */}
      <Route path="admin" element={<AdminPage />} />
    </Routes>
  )
}
