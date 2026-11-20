// UI-04 AI 작품 소개 (API-004, FR-108~111, FR-707): 스포일러 단계를 고르고 요청한다.
// 근거와 맞지 않는 문장은 서버가 지우고(FR-110), 남은 문장이 2개 미만이면 원문 줄거리와 촬영지로 바꿔 보여 준다.
import { useState } from 'react'
import { api } from '../api/client.ts'
import { KMDB_CREDIT, type SpoilerLevel, type WorkIntroResponse } from '../api/works.ts'
import { useNotice } from '../lib/notice.ts'
import { aiErrorMessage } from '../lib/ai.ts'
import { AiBadge } from './ai.tsx'
import { ErrorBox, Loading } from './common.tsx'

const LEVELS: { value: SpoilerLevel; label: string; hint: string }[] = [
  { value: 'none', label: '스포일러 없음', hint: '줄거리 없이 촬영지 이야기만' },
  { value: 'partial', label: '일부 포함', hint: '장면 설명을 조금 넣어요. 결말·반전은 빼요' },
]

export function WorkIntro({ workId, eligible }: { workId: number; eligible: boolean }) {
  const { show } = useNotice()
  const [level, setLevel] = useState<SpoilerLevel>('none') // FR-108: 기본값 '없음'
  const [res, setRes] = useState<WorkIntroResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!eligible)
    return (
      <section className="intro" aria-labelledby="intro-title">
        <h2 id="intro-title" className="sub">
          AI 작품 소개
        </h2>
        <p className="notice small">KMDb와 연결된 작품만 AI 소개를 제공해요.</p>
      </section>
    )

  async function ask(next: SpoilerLevel) {
    setLevel(next)
    setBusy(true)
    setError(null)
    try {
      setRes(await api.post<WorkIntroResponse>(`/works/${workId}/intro`, { spoiler: next }))
    } catch (e) {
      setRes(null)
      setError(aiErrorMessage(e, show))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="intro" aria-labelledby="intro-title">
      <div className="intro-head">
        <h2 id="intro-title" className="sub">
          AI 작품 소개
        </h2>
        <div className="seg" role="radiogroup" aria-label="스포일러 단계">
          {LEVELS.map((l) => (
            <button
              key={l.value}
              role="radio"
              aria-checked={level === l.value}
              className={level === l.value ? 'on' : ''}
              onClick={() => (res ? ask(l.value) : setLevel(l.value))}
              disabled={busy}
            >
              {l.label}
            </button>
          ))}
        </div>
      </div>
      <p className="small muted">{LEVELS.find((l) => l.value === level)!.hint}</p>

      {!res && !busy && (
        <button className="btn primary" onClick={() => ask(level)}>
          AI 소개 보기
        </button>
      )}
      {busy && <Loading rows={2} />}
      <ErrorBox error={error} />

      {res && !busy && res.status === 'ok' && (
        <div className="intro-body">
          <AiBadge />
          {res.sentences.map((s, i) => (
            <p key={i}>{s}</p>
          ))}
          <p className="small muted">
            KMDb 작품 정보와 부산 촬영지 목록만 근거로 만들었어요.
            {res.dropped_count > 0 && ` 근거와 맞지 않는 문장 ${res.dropped_count}개는 뺐어요.`}
          </p>
        </div>
      )}
      {res && !busy && res.status === 'degraded' && (
        <div className="intro-body">
          <p className="notice small" role="note">
            {res.notice ?? 'AI 소개를 만들지 못해 원문 줄거리와 촬영지를 보여 드려요'}
          </p>
          {res.fallback?.synopsis && (
            <>
              <p>{res.fallback.synopsis}</p>
              <p className="small muted">출처: {KMDB_CREDIT}</p>
            </>
          )}
          {!!res.fallback?.locations.length && (
            <p className="small">
              부산 촬영지: {res.fallback.locations.map((l) => (l.gu ? `${l.name}(${l.gu})` : l.name)).join(', ')}
            </p>
          )}
        </div>
      )}
      {res && !busy && res.status === 'not_eligible' && <p className="notice small">{res.notice}</p>}
    </section>
  )
}
