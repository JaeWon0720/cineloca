type PlaceholderPageProps = {
  title: string
  screenId: string
  requirements: string
  description: string
}

// 기능 구현 전 임시 화면. 각 화면 작업에서 실제 내용으로 바꾼다.
export function PlaceholderPage({ title, screenId, requirements, description }: PlaceholderPageProps) {
  return (
    <section className="page">
      <h1>{title}</h1>
      <p className="page-desc">{description}</p>
      <p className="page-meta">
        {screenId} · {requirements}
      </p>
    </section>
  )
}
