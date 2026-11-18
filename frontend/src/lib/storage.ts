// 서버가 보관하지 않는 것을 이 브라우저에만 둔다(ADR-009). 지금은 이용 안내 확인 버전만 저장한다.
// 시크릿 창이나 저장소 차단에서도 앱이 멈추지 않도록 모든 접근을 try/catch로 감싼다.
export const store = {
  get<T>(key: string, fallback?: T): T | undefined {
    try {
      const v = localStorage.getItem(`cineloca:${key}`)
      return v === null ? fallback : (JSON.parse(v) as T)
    } catch {
      return fallback
    }
  },
  set(key: string, value: unknown): boolean {
    try {
      localStorage.setItem(`cineloca:${key}`, JSON.stringify(value))
      return true
    } catch {
      return false
    }
  },
}
