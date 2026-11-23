"""관리자 인증 테스트 (FR-615): 해시, 5회 연속 실패 10분 잠금, 2시간 세션, 로그아웃."""

import pytest

from app.services.admin_auth import (
    LOCK_S,
    SESSION_TTL_S,
    AdminAuth,
    AdminBadPassword,
    AdminLocked,
    check_hash_format,
    hash_password,
    verify_password,
)

PASSWORD = "correct horse battery"
HASH = hash_password(PASSWORD, iterations=1_000)  # 테스트는 반복 수를 줄여 빠르게


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(clock: Clock | None = None) -> tuple[AdminAuth, Clock]:
    clock = clock or Clock()
    return AdminAuth(HASH, clock=clock), clock


def test_hash_has_no_dollar_and_verifies() -> None:
    assert "$" not in HASH and HASH.startswith("pbkdf2_sha256:1000:")
    assert verify_password(PASSWORD, HASH)
    assert not verify_password("wrong", HASH)
    assert hash_password(PASSWORD) != hash_password(PASSWORD)  # salt가 매번 다름
    assert not verify_password(PASSWORD, "pbkdf2_sha256:x:y")  # 형식이 깨지면 False


@pytest.mark.parametrize("bad", ["", "plain-password", "pbkdf2_sha256:abc:salt:hash", "md5:1:a:b"])
def test_plain_password_in_env_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        check_hash_format(bad)


def test_login_issues_two_hour_session() -> None:
    auth, clock = make()

    session = auth.login(PASSWORD)

    assert session.expires_at == int(clock.t) + SESSION_TTL_S
    assert auth.verify(session.token) == session.expires_at
    clock.t += SESSION_TTL_S - 1
    assert auth.verify(session.token) is not None
    clock.t += 1
    assert auth.verify(session.token) is None  # 2시간 지나면 만료


def test_five_failures_lock_for_ten_minutes_even_with_right_password() -> None:
    auth, clock = make()
    for remaining in (4, 3, 2, 1):
        with pytest.raises(AdminBadPassword) as e:
            auth.login("wrong")
        assert e.value.remaining == remaining
    with pytest.raises(AdminLocked) as locked:
        auth.login("wrong")  # 5번째 실패 → 잠금
    assert locked.value.retry_after == LOCK_S

    clock.t += LOCK_S - 30
    with pytest.raises(AdminLocked) as still:
        auth.login(PASSWORD)  # 잠금 중에는 맞는 비밀번호도 거부
    assert 0 < still.value.retry_after <= 31

    clock.t += 30
    assert auth.login(PASSWORD).token  # 10분 뒤 다시 가능


def test_success_resets_failure_count() -> None:
    auth, _ = make()
    for _ in range(4):
        with pytest.raises(AdminBadPassword):
            auth.login("wrong")
    auth.login(PASSWORD)
    with pytest.raises(AdminBadPassword) as e:
        auth.login("wrong")
    assert e.value.remaining == 4  # '연속' 실패만 센다


@pytest.mark.parametrize(
    "token",
    [None, "", "abc", "1.2", "x.y.z", "9999999999.nonce.sig"],
)
def test_malformed_or_forged_tokens_are_rejected(token: str | None) -> None:
    auth, _ = make()
    assert auth.verify(token) is None


def test_tampered_expiry_and_other_password_are_rejected() -> None:
    auth, _ = make()
    token = auth.login(PASSWORD).token
    expires, nonce, sig = token.split(".")
    assert (
        auth.verify(f"{int(expires) + 3600}.{nonce}.{sig}") is None
    )  # 만료를 늘려도 서명이 안 맞음
    other = AdminAuth(hash_password("another password", iterations=1_000))
    assert other.verify(token) is None  # 비밀번호를 바꾸면 기존 세션 무효


def test_logout_revokes_token_until_expiry() -> None:
    auth, _ = make()
    a, b = auth.login(PASSWORD), auth.login(PASSWORD)

    auth.logout(a.token)

    assert auth.verify(a.token) is None
    assert auth.verify(b.token) is not None  # 다른 세션은 그대로
    auth.logout("garbage")  # 이상한 값은 무시


def test_hundred_tampered_tokens_are_all_rejected() -> None:  # NFR-011과 같은 기준
    auth, _ = make()
    token = auth.login(PASSWORD).token
    rejected = 0
    for i in range(100):
        pos = i % len(token)
        swap = "A" if token[pos] != "A" else "B"
        rejected += auth.verify(token[:pos] + swap + token[pos + 1 :]) is None
    assert rejected == 100
