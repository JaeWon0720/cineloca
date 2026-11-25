// 관리 화면 자리 (AdminLayout 안에서만 보인다). 각 화면 작업에서 실제 내용으로 바꾼다.
// 관리 API는 useAdmin().guard(api.get(...))처럼 감싸 부르면, 세션이 끝났을 때 로그인 화면으로 돌아간다.
import { PlaceholderPage } from '../../components/PlaceholderPage.tsx'

export function AdminCoursesPage() {
  return (
    <PlaceholderPage
      title="추천 코스 관리"
      screenId="UI-12"
      requirements="FR-614, API-024"
      description="테마별 추천 코스 후보를 보고 고쳐서 승인합니다."
    />
  )
}
