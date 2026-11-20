// 방문 인증 안내 문구 (FR-503·505·506). 서버 판정 결과를 사람이 읽는 문장으로 바꾼다.
import type { CertExcludedReason } from '../api/locations.ts'
import { MAX_ACCURACY_M, type StampResponse } from '../api/stamps.ts'

export const EXCLUDED_REASON: Record<CertExcludedReason, string> = {
  no_coordinates: '위치를 확인하지 못한 곳이라 방문 인증 대상이 아니에요.',
  private: '사유지·영업 시설이라 방문 인증 대상이 아니에요.',
  residential: '주거지라 방문 인증 대상이 아니에요.',
  school: '학교라 방문 인증 대상이 아니에요.',
  excluded: '방문 인증 대상이 아닌 곳이에요.',
}

export type Msg = { kind: 'ok' | 'warn' | 'error'; text: string }

export function stampMessage(r: StampResponse, radiusM: number, accuracyM: number): Msg {
  if (r.ok) return { kind: 'ok', text: '인증했어요. 스탬프북에 담았어요.' }
  switch (r.reason) {
    case 'low_accuracy': // FR-503: 정확도 50m 초과면 거리 대신 실외 안내
      return { kind: 'warn', text: `위치 정확도가 낮아요(약 ${Math.round(accuracyM)}m, ${MAX_ACCURACY_M}m 이하 필요). 하늘이 보이는 실외로 나가 다시 시도해 주세요.` }
    case 'out_of_range':
      return { kind: 'warn', text: `촬영지까지 약 ${r.distance_m ?? '?'}m 남았어요. 반경 ${radiusM}m 안에서 다시 시도해 주세요.` }
    case 'already_certified': // FR-505
      return { kind: 'warn', text: '이미 인증한 장소예요. 스탬프북에서 볼 수 있어요.' }
    default:
      return { kind: 'error', text: '인증하지 못했어요. 잠시 후 다시 시도해 주세요.' }
  }
}
