// 화면 공통 조각: 출처 카드(FR-704), 빈 상태·불러오는 중·오류 표시
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import type { SourceCard } from '../api/types.ts'

export function Sources({ items }: { items?: SourceCard[] }) {
  if (!items?.length) return null
  return (
    <details className="sources">
      <summary>출처 {items.length}건</summary>
      <ul>
        {items.map((s) => (
          <li key={s.id}>
            {s.url ? (
              <a href={s.url} target="_blank" rel="noreferrer">
                {s.provider} · {s.name}
              </a>
            ) : (
              <span>
                {s.provider} · {s.name}
              </span>
            )}
            {s.base_date && <small> ({s.base_date})</small>}
          </li>
        ))}
      </ul>
    </details>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

/** 불러오는 동안 목록 자리만 잡아 둔다. 화면 낭독기에는 '불러오는 중'을 읽어 준다. */
export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton" role="status">
      <span className="visually-hidden">불러오는 중</span>
      {Array.from({ length: rows }, (_, i) => (
        <span key={i} aria-hidden />
      ))}
    </div>
  )
}

export function ErrorBox({ error }: { error: string | null }) {
  return error ? (
    <p className="error" role="alert">
      {error}
    </p>
  ) : null
}

export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="back">
      ← {children}
    </Link>
  )
}
