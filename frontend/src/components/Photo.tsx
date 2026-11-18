// 외부 이미지: 주소로 직접 표시하고 저장하지 않는다(FR-104, BR-01·03). 주소가 없거나 응답하지 않으면 기본 그림을 보여 준다.
import { useState } from 'react'
import './Photo.css'

export function Photo({ src, alt, className = 'photo' }: { src?: string | null; alt: string; className?: string }) {
  const [broken, setBroken] = useState(false)
  if (!src || broken)
    return (
      <div className={`${className} photo-empty`} role="img" aria-label={alt}>
        <span aria-hidden>사진 준비 중</span>
      </div>
    )
  return <img className={className} src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} />
}
