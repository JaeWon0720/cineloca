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


@pytest.mark.parametrize(
    ("name", "located", "caution", "reason"),
    [
        ("[폐업] 삼중다방(중앙동)", True, None, "closed"),
        (" [철거] 옛 시장", True, "school", "closed"),  # 없어진 곳이 주의 장소보다 먼저
        ("[페업] 대호피엔씨 부산공장(학장동)", True, None, "closed"),  # 원본 데이터 오타
        ("[폐업] 좌표 없는 곳", False, None, "no_coordinates"),  # 좌표 없음이 가장 먼저
        ("해운대해수욕장", True, None, None),
        ("폐업한 가게 앞 골목", True, None, None),  # 앞의 [폐업] 표시만 본다
        ("구) 부산시청", True, None, None),  # 옛 이름 표시는 제외하지 않음
    ],
)
def test_cert_info_closed_place(
    name: str, located: bool, caution: str | None, reason: str | None
) -> None:
    """[폐업]·[철거] 촬영지는 그 장소가 없어 방문 인증을 하지 않는다(BR-06)."""
    info = cert_info(located, caution, False, 100, name)

    assert info["excluded_reason"] == reason
    assert info["eligible"] is (reason is None)
