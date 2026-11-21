"""관리자 인증 (FR-615, API-020, SDD 3.2).

- 비밀번호: 서버 환경변수 ADMIN_PASSWORD_HASH에 해시만 둔다(NFR-009).
  원문은 어디에도 저장하지 않는다. 해시 형식 'pbkdf2_sha256:<반복 수>:<salt>:<hash>'
  (표준 라이브러리 PBKDF2-SHA256, '$'가 없어 docker compose .env에서도 그대로 쓴다).
  만들기: backend 폴더에서 python scripts/admin_password_hash.py
- 잠금: 5회 연속 실패하면 10분 동안 맞는 비밀번호도 받지 않는다. 관리자 비밀번호가
  하나라 잠금도 서버 전체에 하나다(API 워커 1개, 재시작하면 풀림).
- 세션: 서명한 토큰을 쿠키(HttpOnly·Secure·SameSite=Strict, 2시간)로 준다.
  서명 키는 해시에서 만들어서, 비밀번호를 바꾸면 이미 발급한 세션이 모두 무효가 된다.
  로그아웃한 토큰은 만료까지 거부한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
SESSION_TTL_S = 2 * 60 * 60  # 2시간(SDD 3.2)
MAX_FAILURES = 5  # FR-615
LOCK_S = 10 * 60  # FR-615: 10분
MAX_PASSWORD_CHARS = 200

Clock = Callable[[], float]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, iterations: int = ITERATIONS, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{ALGORITHM}:{iterations}:{_b64(salt)}:{_b64(digest)}"


def check_hash_format(stored: str) -> None:
    """설정값이 해시 형식인지 본다. 아니면 ValueError(원문 비밀번호를 넣은 실수 등)."""
    parts = stored.split(":")
    if len(parts) != 4 or parts[0] != ALGORITHM or not parts[1].isdigit():
        raise ValueError("ADMIN_PASSWORD_HASH 형식이 아닙니다")
    _unb64(parts[2]), _unb64(parts[3])


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split(":")
        expected = _unb64(digest)
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


class AdminLocked(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("locked")
        self.retry_after = retry_after


class AdminBadPassword(Exception):
    def __init__(self, remaining: int) -> None:
        super().__init__("bad password")
        self.remaining = remaining


@dataclass(frozen=True)
class AdminSession:
    token: str
    expires_at: float  # epoch 초


class AdminAuth:
    """로그인·세션 확인. 프로세스에 하나(잠금 상태를 공유)."""

    def __init__(self, stored_hash: str, *, clock: Clock = time.time) -> None:
        check_hash_format(stored_hash)
        self._stored = stored_hash
        self._key = hashlib.sha256(b"cineloca-admin-session:" + stored_hash.encode()).digest()
        self._clock = clock
        self._lock = (
            threading.Lock()
        )  # 로그인은 스레드 풀에서 돈다(PBKDF2가 이벤트 루프를 막지 않게)
        self._failures = 0
        self._locked_until = 0.0
        self._revoked: dict[str, float] = {}  # 로그아웃한 토큰의 nonce → 만료 시각

    # ── 로그인(5회 연속 실패 잠금) ─────────────────────────
    def login(self, password: str) -> AdminSession:
        with self._lock:
            now = self._clock()
            if now < self._locked_until:
                raise AdminLocked(int(self._locked_until - now) + 1)
            if len(password) <= MAX_PASSWORD_CHARS and verify_password(password, self._stored):
                self._failures = 0
                return self._issue(now)
            self._failures += 1
            if self._failures >= MAX_FAILURES:
                self._failures = 0
                self._locked_until = now + LOCK_S
                raise AdminLocked(LOCK_S)
            raise AdminBadPassword(MAX_FAILURES - self._failures)

    # ── 세션 토큰 ────────────────────────────────────────
    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())

    def _issue(self, now: float) -> AdminSession:
        expires = int(now) + SESSION_TTL_S
        body = f"{expires}.{secrets.token_urlsafe(16)}"
        return AdminSession(f"{body}.{self._sign(body)}", float(expires))

    def verify(self, token: str | None) -> float | None:
        """맞는 토큰이면 만료 시각, 아니면 None(위조·만료·로그아웃·비밀번호 변경)."""
        if not token or token.count(".") != 2:
            return None
        expires, nonce, sig = token.split(".")
        if not expires.isdigit() or not hmac.compare_digest(sig, self._sign(f"{expires}.{nonce}")):
            return None
        now = self._clock()
        if int(expires) <= now or nonce in self._revoked:
            return None
        return float(expires)

    def logout(self, token: str | None) -> None:
        expires = self.verify(token)
        if expires is None or token is None:
            return
        with self._lock:
            now = self._clock()
            self._revoked = {n: t for n, t in self._revoked.items() if t > now}
            self._revoked[token.split(".")[1]] = expires
