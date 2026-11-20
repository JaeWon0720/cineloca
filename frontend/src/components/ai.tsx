// AI 문장 공통 표시: 'AI 생성' 표시(FR-707)

export function AiBadge() {
  return (
    <span className="ai-badge" title="AI가 시스템이 찾은 근거로 만든 문장이에요. 틀릴 수 있으니 출처를 함께 확인하세요.">
      AI 생성
    </span>
  )
}
