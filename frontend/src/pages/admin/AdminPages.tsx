// 관리 화면 자리 (AdminLayout 안에서만 보인다). 각 화면 작업에서 실제 내용으로 바꾼다.
// 관리 API는 useAdmin().guard(api.get(...))처럼 감싸 부르면, 세션이 끝났을 때 로그인 화면으로 돌아간다.
import { PlaceholderPage } from '../../components/PlaceholderPage.tsx'

export function AdminSyncPage() {
  return (
    <PlaceholderPage
      title="동기화 리포트"
      screenId="UI-10"
      requirements="FR-612·613·616, API-021"
      description="데이터 동기화를 요청하고 최근 실행 결과(추가·변경·실패 건수)를 봅니다."
    />
  )
}

export function AdminReviewPage() {
  return (
    <PlaceholderPage
      title="검수 큐·속성 편집"
      screenId="UI-11"
      requirements="FR-609·610, API-022·023"
      description="C1이 넣은 검수 항목을 승인·거절하고, 촬영지 속성(인증 제외 등)을 고칩니다."
    />
  )
}

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
