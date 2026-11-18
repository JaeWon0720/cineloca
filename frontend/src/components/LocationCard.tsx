// 촬영지 카드: 거리순 목록에 쓴다 (FR-206: 장소명·대표 작품 최대 3편·거리·최근접 역).
// 번호는 거리순 순위이고 지도 핀 번호와 같다.
import { Link } from 'react-router'
import type { LocationHit } from '../api/locations.ts'
import { fmtDist, stationLabel } from '../lib/geo.ts'

function worksLine(loc: LocationHit) {
  const more = loc.work_count - loc.works.length
  return `${loc.works.map((w) => `「${w.title}」`).join(' ')}${more > 0 ? ` 외 ${more}편` : ''}`
}

/** 대표 작품 최대 3편과 '외 N편' (FR-206) */
export function WorksLine({ loc }: { loc: LocationHit }) {
  return <>{worksLine(loc)}</>
}

export function LocationCard({ loc, rank, onClick, active }: { loc: LocationHit; rank?: number; onClick?: () => void; active?: boolean }) {
  const st = loc.nearest_station
  const body = (
    <>
      {rank !== undefined && (
        <span className="rank" aria-hidden>
          {rank}
        </span>
      )}
      <span className="loc-body">
        <span className="loc-top">
          <strong>{loc.name}</strong>
          <span className="dist">{fmtDist(loc.distance_m)}</span>
        </span>
        <span className="loc-works">{worksLine(loc)}</span>
        <span className="meta">
          {loc.gu && <span>{loc.gu}</span>}
          {st && st.walk_min <= 30 && (
            <span>
              {stationLabel(st.name)} 도보 {Math.round(st.walk_min)}분
            </span>
          )}
        </span>
      </span>
    </>
  )
  return onClick ? (
    <button type="button" className={`card loc${active ? ' active' : ''}`} onClick={onClick} aria-pressed={active}>
      {body}
    </button>
  ) : (
    <Link className="card loc" to={`/locations/${loc.id}`}>
      {body}
    </Link>
  )
}
