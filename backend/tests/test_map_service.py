"""MapService 업무 규칙 단위 테스트 (BR-06·07, FR-209, 장르 묶음 펼치기)."""

import pytest

from app.core.genres import GENRE_GROUPS
from app.services.map_service import CAUTION_NOTICES, cert_info, expand_genres


def test_expand_genres_to_kmdb_names() -> None:
    assert expand_genres(["범죄·느와르"]) == sorted(GENRE_GROUPS["범죄·느와르"])
    assert expand_genres(["공포", "공포"]) == ["공포", "공포(호러)"]
    assert expand_genres([]) == []


@pytest.mark.parametrize(
    ("located", "caution", "excluded", "reason"),
    [
        (True, None, False, None),
        (False, None, False, "no_coordinates"),  # 좌표 없음이 먼저
        (False, "school", True, "no_coordinates"),
        (True, "residential", False, "residential"),
        (True, "private", True, "private"),
        (True, None, True, "excluded"),  # 관리자가 제외(FR-610)
    ],
)
def test_cert_info(located: bool, caution: str | None, excluded: bool, reason: str | None) -> None:
    info = cert_info(located, caution, excluded, 150)

    assert info == {"eligible": reason is None, "radius_m": 150, "excluded_reason": reason}


def test_every_caution_has_outside_only_notice() -> None:
    assert set(CAUTION_NOTICES) == {"private", "residential", "school"}
    assert all("외부에서만 관람" in text for text in CAUTION_NOTICES.values())  # FR-209
