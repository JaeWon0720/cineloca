// UI-09 데이터 출처·이용 안내 (UC-13, FR-705)
import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client.ts'
import type { SourcesInfo } from '../api/types.ts'
import { ErrorBox } from '../components/common.tsx'
import { useNotice } from '../lib/notice.ts'

const POLICY: Record<string, string> = { STORE: '저장', ID_ONLY: 'ID만 저장', NO_STORE: '저장 안 함' }

export default function SourcesPage() {
  const notice = useNotice()
  const [d, setD] = useState<SourcesInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<SourcesInfo>('/sources')
      .then(setD)
      .catch((e: Error) =>
        // API-019(#111)가 서버에 아직 없으면 404가 온다
        setError(e instanceof ApiError && e.status === 404 ? '데이터 출처 정보가 아직 서버에 준비되지 않았어요. 각 결과의 출처 카드에서 제공기관과 원문 링크를 볼 수 있어요.' : e.message),
      )
  }, [])

  return (
    <section className="page">
      <header className="page-head">
        <h1>데이터 출처</h1>
        <p className="muted small">모든 결과에는 출처 카드가 붙고, 아래는 서비스가 쓰는 데이터의 이용조건과 저장 정책입니다.</p>
        <div>
          <button className="btn small" onClick={notice.show}>
            이용 안내 다시 보기
          </button>
        </div>
      </header>
      <ErrorBox error={error} />
      <h2 className="sub">출처 대장</h2>
      <div className="table-wrap">
        <table className="ledger">
          <thead>
            <tr>
              <th>제공</th>
              <th>데이터·문서</th>
              <th>기준일</th>
              <th>이용조건</th>
              <th>저장</th>
            </tr>
          </thead>
          <tbody>
            {d?.ledger.map((s) => (
              <tr key={s.id}>
                <td data-label="제공">{s.provider}</td>
                <td data-label="데이터">
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noreferrer">
                      {s.name}
                    </a>
                  ) : (
                    s.name
                  )}
                </td>
                <td className="small" data-label="기준일">
                  {s.base_date ?? '—'}
                </td>
                <td className="small" data-label="이용조건">
                  {s.license}
                </td>
                <td className="small" data-label="저장">
                  {POLICY[s.policy] ?? s.policy}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2 className="sub">오픈소스·모델 라이선스</h2>
      <ul className="small">
        {d?.licenses.map((l) => (
          <li key={l.name}>
            {l.name} — {l.license}
          </li>
        ))}
      </ul>
    </section>
  )
}
