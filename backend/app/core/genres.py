"""장르 묶음표 (FR-205 장르 필터, FR-310 필터 패널).

KMDb 장르는 38종으로 잘게 나뉘어 있어 비슷한 것끼리 12개로 묶어 필터에 보여 준다(2026-11-13 결정).
작품 하나는 여러 묶음에 속할 수 있고, 필터는 고른 묶음 중 하나라도 맞으면 고른다.
묶음표에 없는 새 KMDb 장르는 필터에서 빼고 동기화 리포트에 경고로 남긴다(FR-613).
근거: 착수 PoC에서 KMDb에 연결된 작품 356편의 장르 전체(38종).
"""

from typing import Literal

GenreGroup = Literal[
    "드라마",
    "액션·모험",
    "스릴러·미스터리",
    "범죄·느와르",
    "코미디",
    "멜로·로맨스",
    "SF·판타지",
    "공포",
    "시대극·전쟁",
    "가족",
    "청춘·스포츠",
    "재난",
]

# 묶음 → KMDb 장르. 묶음 순서가 필터에 보이는 순서다(작품 수 많은 순, 재난은 부산 대표 장르라 별도).
GENRE_GROUPS: dict[GenreGroup, tuple[str, ...]] = {
    "드라마": (
        "드라마",
        "사회물(경향)",
        "사회",
        "인권",
        "전기",
        "인물",
        "로드무비",
        "문화",
        "뮤지컬",
        "뮤직",
    ),
    "액션·모험": ("액션", "해양액션", "무협", "어드벤처"),
    "스릴러·미스터리": ("스릴러", "미스터리", "첩보"),
    "범죄·느와르": ("범죄", "느와르", "갱스터"),
    "코미디": ("코메디",),
    "멜로·로맨스": ("멜로드라마", "멜로/로맨스", "신파"),
    "SF·판타지": ("SF", "판타지"),
    "공포": ("공포", "공포(호러)"),
    "시대극·전쟁": ("시대극/사극", "역사", "전쟁", "군사", "반공/분단"),
    "가족": ("가족",),
    "청춘·스포츠": ("청춘영화", "하이틴(고교)", "스포츠"),
    "재난": ("재난",),
}

_GROUP_OF: dict[str, GenreGroup] = {
    kmdb: group for group, members in GENRE_GROUPS.items() for kmdb in members
}


def group_genres(kmdb_genres: str | list[str]) -> tuple[list[GenreGroup], list[str]]:
    """KMDb 장르(쉼표 구분 문자열 또는 목록)를 묶음으로 바꾼다.

    돌려주는 값: (묶음 목록(묶음표 순서), 묶음표에 없는 장르 목록(리포트 경고용))
    """
    if isinstance(kmdb_genres, str):
        kmdb_genres = kmdb_genres.split(",")
    groups: set[GenreGroup] = set()
    unknown: list[str] = []
    for raw in kmdb_genres:
        name = raw.strip()
        if not name:
            continue
        group = _GROUP_OF.get(name)
        if group:
            groups.add(group)
        elif name not in unknown:
            unknown.append(name)
    return [g for g in GENRE_GROUPS if g in groups], unknown
