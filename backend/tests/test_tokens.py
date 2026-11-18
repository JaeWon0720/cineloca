"""스탬프·배지 서명 토큰 테스트 (TokenSigner, ADR-009, NFR-011: 위조·변조 100건 모두 거부)."""

import base64
import json

import jwt
import pytest

from app.support.tokens import Claims, TokenSigner

KEY = "k" * 40
OTHER_KEYS = [f"other-key-{i:02d}-" + "x" * 30 for i in range(20)]


def signer(key: str = KEY, at: float = 1_763_000_000) -> TokenSigner:
    return TokenSigner(key, now=lambda: at)


def _b64(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_stamp_and_badge_round_trip() -> None:
    s = signer()

    assert s.verify(s.sign_stamp(7)) == Claims("stamp", 1_763_000_000, loc=7)
    assert s.verify(s.sign_badge("course:3")) == Claims(
        "badge", 1_763_000_000, course_key="course:3"
    )


def test_token_has_no_coordinates_or_expiry() -> None:
    payload = jwt.decode(signer().sign_stamp(7), options={"verify_signature": False})

    assert set(payload) == {"typ", "loc", "iat", "kid"}  # FR-504: 좌표 없음, 만료 없음


def test_short_key_is_rejected_and_key_is_not_printed() -> None:
    with pytest.raises(ValueError):
        TokenSigner("short")
    assert KEY not in repr(signer())


def forged_tokens() -> list[object]:
    """공격자가 만들 수 있는 위조·변조 토큰 100개."""
    s = signer()
    good = s.sign_stamp(7)
    header, payload, sig = good.split(".")
    out: list[object] = []
    # 1) 서명 글자 하나 바꾸기 30개
    for i in range(30):
        ch = "A" if sig[i] != "A" else "B"
        out.append(f"{header}.{payload}.{sig[:i]}{ch}{sig[i + 1 :]}")
    # 2) 내용만 바꾸고 원래 서명 붙이기 20개(촬영지·종류·발급 시각·kid 위조)
    for loc in range(1, 11):
        out.append(
            f"{header}.{_b64({'typ': 'stamp', 'loc': 100 + loc, 'iat': 1, 'kid': s.kid})}.{sig}"
        )
    for key in range(10):
        body = {"typ": "badge", "course_key": f"course:{key}", "iat": 1, "kid": s.kid}
        out.append(f"{header}.{_b64(body)}.{sig}")
    # 3) 다른 키로 서명 20개
    for k in OTHER_KEYS:
        out.append(TokenSigner(k).sign_stamp(7))
    # 4) 알고리즘 바꾸기 10개: none, HS384·HS512(같은 키), 헤더만 none으로
    claims = {"typ": "stamp", "loc": 7, "iat": 1_763_000_000, "kid": s.kid}
    out.append(jwt.encode(claims, None, algorithm="none"))
    out.append(jwt.encode(claims, KEY, algorithm="HS384"))
    out.append(jwt.encode(claims, KEY, algorithm="HS512"))
    out.append(f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{payload}.")
    out.append(f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{payload}.{sig}")
    out.append(f"{_b64({'alg': 'HS512', 'typ': 'JWT'})}.{payload}.{sig}")
    out.append(f"{_b64({'typ': 'JWT'})}.{payload}.{sig}")
    out.append(f"{header}.{payload}")
    out.append(f"{header}..{sig}")
    out.append(f".{payload}.{sig}")
    # 5) 깨진 입력 20개
    out += ["", ".", "..", "a.b.c", "null", "[]", "{}", good[:-1], good + "x", good + ".x"]
    out += [
        None,
        7,
        3.5,
        b"bytes",
        ["list"],
        {"t": good},
        True,
        "x" * 5000,
        " " + good,
        good.upper(),
    ]
    return out


# 공격자 쪽에서 HS384·HS512로 만드는 단계의 키 길이 경고는 시험 대상이 아님
@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
def test_rejects_100_forged_or_tampered_tokens() -> None:
    tokens = forged_tokens()

    assert len(tokens) == 100
    assert [t for t in tokens if signer().verify(t) is not None] == []


@pytest.mark.parametrize(
    "claims",
    [
        {"typ": "stamp", "loc": 7, "iat": 1, "kid": "?", "lat": 35.1},  # 모르는 칸(좌표)
        {"typ": "stamp", "loc": "7", "iat": 1},  # 형식 틀림
        {"typ": "stamp", "loc": True, "iat": 1},
        {"typ": "stamp", "loc": 0, "iat": 1},
        {"typ": "badge", "course_key": "", "iat": 1},
        {"typ": "badge", "course_key": "c" * 81, "iat": 1},
        {"typ": "coupon", "loc": 7, "iat": 1},
        {"typ": "stamp", "loc": 7},  # iat 없음
    ],
)
def test_rejects_signed_tokens_with_wrong_shape(claims: dict) -> None:
    s = signer()
    claims = {**claims, "kid": s.kid} if "kid" not in claims else {**claims, "kid": s.kid}

    assert s.verify(jwt.encode(claims, KEY, algorithm="HS256")) is None


def test_key_rotation_invalidates_old_tokens() -> None:
    old = signer().sign_stamp(7)

    assert signer("n" * 40).verify(old) is None
