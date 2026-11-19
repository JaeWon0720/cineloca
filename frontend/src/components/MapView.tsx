// MapView: VITE_KAKAO_JS_KEY가 있으면 카카오 지도 JS SDK(DATA-07), 없으면 Leaflet + OpenStreetMap으로 대체 표시한다.
// 키 없이도 개발할 수 있게 하기 위한 대체 표시이며, 카카오 응답은 그리기만 하고 저장하지 않는다(NO_STORE).
import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import './MapView.css'

export type Marker = { id: number; lat: number; lon: number; label: string; active?: boolean; order?: number }
type Props = {
  center: [number, number]
  markers: Marker[]
  path?: [number, number][]
  radiusM?: number
  me?: [number, number] | null
  onSelect?: (id: number) => void
  height?: number
}

const KAKAO_KEY = import.meta.env.VITE_KAKAO_JS_KEY as string | undefined
const ACCENT = '#1f5f8b' // 바다색(index.css --accent)

// ---------- 카카오 지도
// SDK에는 타입 정의가 없어서 필요한 만큼만 느슨하게 다룬다.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type KakaoMaps = any
declare global {
  interface Window {
    kakao?: { maps: KakaoMaps }
  }
}

let kakaoLoading: Promise<KakaoMaps> | null = null
function loadKakao(): Promise<KakaoMaps> {
  if (window.kakao?.maps?.Map) return Promise.resolve(window.kakao.maps)
  kakaoLoading ??= new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${KAKAO_KEY}&autoload=false&libraries=clusterer`
    s.onload = () => window.kakao!.maps.load(() => resolve(window.kakao!.maps))
    s.onerror = () => reject(new Error('카카오 지도를 불러오지 못했습니다.'))
    document.head.appendChild(s)
  })
  return kakaoLoading
}

// 목록과 같은 번호 핀(.pin)을 카카오 지도에도 그린다.
function pinEl(m: Marker, onSelect?: (id: number) => void): HTMLButtonElement {
  const b = document.createElement('button')
  b.type = 'button'
  b.className = `pin${m.active ? ' pin-active' : ''}`
  b.title = m.label
  b.setAttribute('aria-label', m.label)
  const s = document.createElement('span')
  s.textContent = m.order != null ? String(m.order) : ''
  b.appendChild(s)
  b.addEventListener('click', () => onSelect?.(m.id))
  return b
}

function KakaoMap({ center, markers, path, radiusM, me, onSelect, height = 320 }: Props) {
  const el = useRef<HTMLDivElement>(null)
  // 지도와 마커 묶음은 한 번만 만들고, 아래 효과에서 표시물만 다시 그린다(깜빡임·확대 상태 풀림 방지).
  const kit = useRef<{ k: KakaoMaps; map: KakaoMaps; clusterer: KakaoMaps } | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    loadKakao()
      .then((k) => {
        if (cancelled || !el.current) return
        const map = new k.Map(el.current, { center: new k.LatLng(center[0], center[1]), level: 5 })
        const clusterer = new k.MarkerClusterer({ map, averageCenter: true, minLevel: 6 }) // FR-204 마커 묶음
        kit.current = { k, map, clusterer }
        setReady(true)
      })
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
      kit.current?.clusterer.clear()
      kit.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const c = kit.current
    if (!ready || !c) return
    const { k, map, clusterer } = c
    const drawn: { setMap: (m: null) => void }[] = []
    clusterer.clear()
    clusterer.addMarkers(
      markers.map(
        (m) =>
          new k.CustomOverlay({ position: new k.LatLng(m.lat, m.lon), content: pinEl(m, onSelect), yAnchor: 1, zIndex: m.active ? 3 : 1 }),
      ),
    )
    if (path && path.length > 1)
      drawn.push(new k.Polyline({ map, path: path.map((p) => new k.LatLng(p[0], p[1])), strokeWeight: 4, strokeColor: ACCENT }))
    if (radiusM)
      drawn.push(
        new k.Circle({ map, center: new k.LatLng(center[0], center[1]), radius: radiusM, strokeWeight: 1, strokeColor: ACCENT, strokeOpacity: 0.5, fillColor: ACCENT, fillOpacity: 0.05 }),
      )
    if (me) drawn.push(new k.Marker({ map, position: new k.LatLng(me[0], me[1]), title: '현재 위치' }))
    map.setLevel(radiusM && radiusM > 1000 ? 7 : 5)
    map.setCenter(new k.LatLng(center[0], center[1]))
    return () => drawn.forEach((d) => d.setMap(null))
  }, [ready, center, markers, path, radiusM, me, onSelect])

  if (failed) return <p className="error">카카오 지도를 불러오지 못했습니다. 키 설정과 등록한 도메인을 확인해 주세요.</p>
  return <div ref={el} className="map" style={{ height }} role="application" aria-label="카카오 지도" />
}

// ---------- Leaflet 대체 표시
function LeafletMap({ center, markers, path, radiusM, me, onSelect, height = 320 }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layer = useRef<L.LayerGroup | null>(null)

  useEffect(() => {
    if (!el.current) return
    const m = L.map(el.current, { zoomControl: true, attributionControl: true }).setView(center, 14)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors',
    }).addTo(m)
    map.current = m
    layer.current = L.layerGroup().addTo(m)
    return () => {
      m.remove()
      map.current = null
      layer.current = null
    }
    // 지도는 한 번만 만들고, 아래 효과에서 표시물만 다시 그린다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const m = map.current
    const g = layer.current
    if (!m || !g) return
    g.clearLayers()
    const pts: L.LatLngExpression[] = []
    if (radiusM) L.circle(center, { radius: radiusM, color: ACCENT, weight: 1, fillOpacity: 0.04 }).addTo(g)
    if (me) L.circleMarker(me, { radius: 7, color: '#fff', weight: 3, fillColor: '#1f5f8b', fillOpacity: 1 }).addTo(g).bindTooltip('현재 위치')
    if (path && path.length > 1) L.polyline(path, { color: ACCENT, weight: 4, opacity: 0.85 }).addTo(g)
    for (const mk of markers) {
      const icon = L.divIcon({
        className: '',
        html: `<div class="pin${mk.active ? ' pin-active' : ''}"><span>${mk.order ?? ''}</span></div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 28],
      })
      L.marker([mk.lat, mk.lon], { icon, title: mk.label, keyboard: true })
        .addTo(g)
        .bindTooltip(mk.label)
        .on('click', () => onSelect?.(mk.id))
      pts.push([mk.lat, mk.lon])
    }
    m.invalidateSize() // 화면 폭에 따라 지도 높이가 CSS로 바뀌므로, 맞추기 전에 크기를 다시 잰다
    if (pts.length > 1 && !radiusM) m.fitBounds(L.latLngBounds(pts), { padding: [28, 28], maxZoom: 15 })
    else m.setView(center, radiusM && radiusM > 1000 ? 13 : 14)
  }, [center, markers, path, radiusM, me, onSelect])

  return <div ref={el} className="map" style={{ height }} role="application" aria-label="지도" />
}

export default function MapView(p: Props) {
  return KAKAO_KEY ? <KakaoMap {...p} /> : <LeafletMap {...p} />
}
