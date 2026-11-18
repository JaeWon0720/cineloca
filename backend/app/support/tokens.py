"""스탬프·완주 배지 서명 토큰 (COMP-07 TokenSigner, ADR-009, FR-504·508, NFR-011).

- JWT(HS256). 서버 비밀키(STAMP_SIGNING_KEY)로 서명하고, 확인할 때는 HS256만 받는다.
- stamp {typ, loc, iat, kid}, badge {typ, course_key, iat, kid}. 좌표·만료는 넣지 않는다(SDD 5.2).
- kid는 키의 지문(SHA-256 앞 8자리)이다. 키를 바꾸면 이전 토큰은 kid가 달라 모두 무효가 된다.
- 서버는 토큰을 저장하지 않는다. 브라우저가 보관하고 필요할 때 다시 보낸다.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import jwt

ALGORITHM = "HS256"
MIN_KEY_LENGTH = 32  # HS256 키는 32바이트 이상(RFC 7518 3.2)
MAX_TOKEN_LENGTH = 1000  # 정상 토큰은 200자 안팎. 지나치게 긴 입력은 해석하지 않는다

TokenType = Literal["stamp", "badge"]


@dataclass(frozen=True)
class Claims:
    typ: TokenType
    iat: int
    loc: int | None = None  # stamp: 촬영지 ID
    course_key: str | None = None  # badge: 'course:<id>' 또는 'saved:<지문>'


class TokenSigner:
    def __init__(self, secret: str, now: Callable[[], float] = time.time) -> None:
        if len(secret.encode()) < MIN_KEY_LENGTH:
            raise ValueError(f"STAMP_SIGNING_KEY는 {MIN_KEY_LENGTH}자 이상이어야 합니다")
        self._secret = secret
        self._now = now
        self.kid = hashlib.sha256(secret.encode()).hexdigest()[:8]

    def __repr__(self) -> str:  # 키가 디버그 출력에 찍히지 않게
        return f"TokenSigner(kid={self.kid!r})"

    def sign_stamp(self, location_id: int) -> str:
        return self._sign({"typ": "stamp", "loc": location_id})

    def sign_badge(self, course_key: str) -> str:
        return self._sign({"typ": "badge", "course_key": course_key})

    def _sign(self, body: dict[str, object]) -> str:
        claims = {**body, "iat": int(self._now()), "kid": self.kid}
        return jwt.encode(claims, self._secret, algorithm=ALGORITHM)

    def verify(self, token: object) -> Claims | None:
        """서명·알고리즘·kid·필드 형식이 모두 맞을 때만 Claims. 아니면 None(이유는 밝히지 않음)."""
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
            return None
        try:
            data = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                options={"require": ["iat"], "verify_iat": False},
            )
        except jwt.PyJWTError:
            return None
        if data.get("kid") != self.kid or not _int(data.get("iat")):
            return None
        typ = data.get("typ")
        if typ == "stamp" and _int(data.get("loc")) and set(data) == {"typ", "loc", "iat", "kid"}:
            return Claims("stamp", data["iat"], loc=data["loc"])
        course_key = data.get("course_key")
        if (
            typ == "badge"
            and isinstance(course_key, str)
            and 0 < len(course_key) <= 80
            and set(data) == {"typ", "course_key", "iat", "kid"}
        ):
            return Claims("badge", data["iat"], course_key=course_key)
        return None


def _int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
