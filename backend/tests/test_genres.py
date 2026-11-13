"""장르 묶음표 테스트 (FR-205·310, #27)."""

from typing import get_args

from app.core.genres import GENRE_GROUPS, GenreGroup, group_genres

# 착수 PoC에서 KMDb에 연결된 작품 356편에 나온 장르 전체(38종)
POC_GENRES = (
    "드라마 액션 코메디 범죄 스릴러 미스터리 멜로드라마 느와르 공포 판타지 SF 멜로/로맨스 가족 "
    "시대극/사극 어드벤처 재난 스포츠 청춘영화 전쟁 첩보 사회물(경향) 하이틴(고교) 로드무비 "
    "공포(호러) 무협 전기 뮤지컬 갱스터 문화 사회 인물 신파 군사 반공/분단 해양액션 뮤직 인권 역사"
).split()


def test_groups_match_literal_and_have_no_duplicates() -> None:
    members = [g for group in GENRE_GROUPS.values() for g in group]

    assert list(GENRE_GROUPS) == list(get_args(GenreGroup))
    assert len(members) == len(set(members))


def test_every_poc_genre_belongs_to_a_group() -> None:
    assert len(POC_GENRES) == 38
    groups, unknown = group_genres(POC_GENRES)

    assert unknown == []
    assert groups == list(GENRE_GROUPS)


def test_group_genres_from_kmdb_string() -> None:
    groups, unknown = group_genres("멜로/로맨스, 드라마,멜로드라마, ,웹툰원작")

    assert groups == ["드라마", "멜로·로맨스"]  # 묶음표 순서, 중복 없음
    assert unknown == ["웹툰원작"]  # 리포트 경고용
    assert group_genres("") == ([], [])
