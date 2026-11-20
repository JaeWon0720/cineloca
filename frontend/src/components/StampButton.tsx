// F5 방문 인증 버튼 (API-016, FR-501~506). 촬영지 상세와 코스 상세에서 쓴다.
// 위치는 이번 요청에만 쓰고 저장하지 않는다(BR-10). 판정(반경·정확도 50m)은 서버가 한다(FR-502).
import { useState } from 'react'
import { Link } from 'react-router'
import { ApiError, api } from '../api/client.ts'
import type { CertInfo } from '../api/locations.ts'
import type { StampResponse } from '../api/stamps.ts'
import { getPosition } from '../lib/geo.ts'
import { EXCLUDED_REASON, stampMessage, type Msg } from '../lib/stamp.ts'
import { tokens } from '../lib/wallet.ts'

export function StampButton({
  locationId,
  cert,
  done = false,
  onStamped,
  compact = false,
}: {
  locationId: number
  cert: CertInfo
  done?: boolean
  onStamped?: () => void
  compact?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<Msg | null>(null)

  // FR-506: 인증 제외 장소는 버튼을 막고 이유를 보여 준다
  if (!cert.eligible)
    return (
      <div className="stamp">
        <button className={`btn${compact ? ' small' : ''}`} disabled>
          방문 인증
        </button>
        <p className="small muted">{EXCLUDED_REASON[cert.excluded_reason ?? 'excluded']}</p>
      </div>
    )
  if (done)
    return (
      <div className="stamp">
        <span className="stamp-done">인증 완료</span>
        {!compact && (
          <Link className="small" to="/me">
            스탬프북에서 보기
          </Link>
        )}
      </div>
    )

  async function certify() {
    setBusy(true)
    setMsg(null)
    try {
      const p = await getPosition(10000)
      const r = await api.post<StampResponse>('/stamps', {
        location_id: locationId,
        lat: p.lat,
        lon: p.lon,
        accuracy_m: p.accuracy,
        held: tokens.all(),
      })
      if (r.ok && r.token) {
        if (!tokens.add(r.token)) {
          setMsg({ kind: 'error', text: '인증은 됐지만 이 브라우저에 저장하지 못했어요. 시크릿 창이면 일반 창에서 다시 해 주세요.' })
          return
        }
        onStamped?.()
      }
      setMsg(stampMessage(r, cert.radius_m, p.accuracy))
    } catch (e) {
      if (e instanceof ApiError) setMsg({ kind: 'error', text: e.message }) // CERT_EXCLUDED(409), STAMP_UNAVAILABLE(503) 등
      else setMsg({ kind: 'error', text: e instanceof Error ? e.message : '위치를 확인하지 못했어요.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stamp">
      <button className={`btn primary${compact ? ' small' : ''}`} onClick={certify} disabled={busy}>
        {busy ? '위치 확인 중…' : '방문 인증'}
      </button>
      {!compact && !msg && <p className="small muted">촬영지 반경 {cert.radius_m}m 안에서 누르세요. 위치는 저장하지 않아요.</p>}
      {msg && (
        <p className={`small stamp-msg ${msg.kind}`} role={msg.kind === 'ok' ? 'status' : 'alert'}>
          {msg.text}
        </p>
      )}
    </div>
  )
}
