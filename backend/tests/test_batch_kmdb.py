"""KMDb 매칭 단위 테스트 (FR-603·604, BR-11). 실제 KMDb 호출 없음.

v2 테스트에는 2026-11-13 실제 실행에서 찾은 오류 사례(박쥐, 브로커, 특근, KBS 아이언맨 등)를
그대로 넣었다.
"""

import asyncio
import csv

from app.batch import kmdb
from app.batch.kmdb import Candidate, KmdbCsv, decide, rule_c, score


def C(title: str, **kw) -> Candidate:  # noqa: N802
    return Candidate(docid=kw.pop("docid", "K" + title), title=title, **kw)


def test_score_weights_title_and_year() -> None:
    assert score("친구", 2001, C("친구", rel_date="20010331")) == 1.0
    assert score("친구", 2001, C("친구", rel_date="20020101")) == 0.875  # ±1년
    assert score("친구", None, C("친구")) == 0.875  # 연도 모름 0.5
    assert score("친구", 2001, C("친구", prod_year="2000")) == 1.0  # 제작연도는 전해 허용
    assert score("친구", 2001, C("친구", rel_date="19900101")) == 0.75


def test_decide_thresholds() -> None:
    assert decide("친구", 2001, [C("친구", rel_date="2001")], set()).status == "matched"
    assert decide("친구", 2001, [C("친구", rel_date="1990")], set()).status == "review"
    assert decide("친구", 2001, [C("전혀다른제목", rel_date="1990")], set()).status == "unmatched"
    assert decide("친구", 2001, [], set()) == kmdb.MatchResult("unmatched", "none", 0.0, None)


def test_rule_c_needs_exact_title_busan_evidence_and_single_candidate() -> None:
    busan = C("변호인", f_location="부산 중구")
    place = C("변호인", docid="K2", f_location="영도다리,서울")
    other = C("변호인", docid="K3", f_location="서울")
    assert rule_c("변호인", [busan, other], set()) == busan
    assert rule_c("변호인", [place], {"영도다리"}) == place
    assert rule_c("변호인", [busan, place], {"영도다리"}) is None  # 둘이면 자동 연결 안 함
    assert rule_c("변호인", [other], set()) is None
    alias = C("Attorney", title_etc="변호인^The Attorney", f_location="부산")
    assert rule_c("변호인", [alias], set()) == alias
    # 개봉일 없는 작품: 점수 0.875(< 0.9)라도 규칙 C로 자동 연결, 후보가 둘이면 검수
    assert decide("변호인", None, [busan], set()).rule == "rule_c"
    assert decide("변호인", None, [busan, place], {"영도다리"}).status == "review"
    # 개봉연도가 있으면 규칙 C를 쓰지 않는다
    assert (
        decide("변호인", 2013, [C("변호인", f_location="부산", rel_date="2000")], set()).status
        == "review"
    )


def test_rule_c_used_when_year_unknown() -> None:
    c = C("부산행 외전", title_etc="그곳^부산", f_location="부산")
    res = decide("그곳", None, [c], set())
    assert (res.status, res.rule, res.best) == ("matched", "rule_c", c)


def test_stored_kmdb_has_no_image_fields() -> None:
    v = kmdb.stored_kmdb(C("친구", plot="p", keywords="k", link="l"))
    assert v == {"plot": "p", "keywords": "k", "link": "l", "source_id": "DATA-04"}


# ---- v2 매칭 보완 (2026-11-13) ----


def test_split_media() -> None:
    assert kmdb.split_media("JTBC 나의 해방일지") == ("나의 해방일지", "JTBC")
    assert kmdb.split_media("넷플릭스  길복순") == ("길복순", "넷플릭스")
    assert kmdb.split_media("시간이탈자") == ("시간이탈자", None)


def test_same_title_picks_theatrical_release_over_short() -> None:
    # 실제 오류 사례(박쥐 2009): 개봉일 없는 단편이 먼저 와도 개봉일이 맞는 극장 영화를 고른다
    short = C("박쥐", docid="K12267", prod_year="2009", use="-", runtime=13)
    film = C(
        "박쥐", docid="K10096", prod_year="2009", rel_date="20090430", use="극장용", runtime=133
    )
    res = decide("박쥐", 2009, [short, film], set())
    assert (res.status, res.best) == ("matched", film)


def test_same_title_same_evidence_goes_to_review() -> None:
    a = C("유령", docid="A", rel_date="19990731", use="극장용", runtime=103)
    b = C("유령", docid="B", rel_date="20230118", use="극장용", runtime=133)
    res = decide("유령", None, [a, b], set())
    assert res.status == "review"


def test_unknown_year_drops_old_candidates_then_rule_u() -> None:
    old = C("숙명", docid="OLD", rel_date="19671006", use="극장용", runtime=96)
    new = C("숙명", docid="NEW", rel_date="20080320", use="극장용", runtime=123)
    res = decide("숙명", None, [old, new], set())
    assert (res.status, res.rule, res.best) == ("matched", "rule_u", new)


def test_rule_u_ignores_shorts_but_not_alone() -> None:
    feat = C("아저씨", docid="F", rel_date="20100804", use="극장용", runtime=119)
    short = C("아저씨", docid="S", prod_year="2015", use="극장용", runtime=20)
    assert decide("아저씨", None, [feat, short], set()).best == feat
    assert decide("특근", None, [C("특근", prod_year="2013", runtime=30)], set()).status == "review"


def test_tv_prefix_never_links_foreign_theatrical_film() -> None:
    # 실제 오류 후보: 'KBS 아이언맨'(드라마) ↔ 아이언 맨(2008, 미국 극장 영화)
    iron = C("아이언 맨", rel_date="20080430", use="극장용", runtime=125, country="미국")
    assert decide("KBS 아이언맨", None, [iron], set()).status != "matched"
    grid = C("그리드", rel_date="20201015", use="극장용", runtime=104, country="영국")
    assert decide("디즈니+ 그리드", None, [grid], set()).status != "matched"
    drama = C("나의 해방일지", prod_year="2022", use="TV용", runtime=1093)
    assert decide("JTBC 나의 해방일지", None, [drama], set()).rule == "rule_u"
    ott_film = C("승리호", rel_date="20210205", use="극장용", runtime=136, country="대한민국")
    assert decide("넷플릭스 승리호", None, [ott_film], set()).status == "matched"


def test_rule_c_uses_plot_or_keywords_when_no_location() -> None:
    a = C("바람", docid="A", rel_date="20091126", use="극장용", runtime=108, plot="부산 고등학생")
    b = C("바람", docid="B", rel_date="20190101", use="극장용", runtime=90, plot="서울")
    res = decide("바람", None, [a, b], set())
    assert (res.status, res.rule, res.best) == ("matched", "rule_c", a)


def test_rule_u_needs_release_date_when_it_beat_other_same_titles() -> None:
    # 실제 사례(특근): 36분 인터넷 영화와 개봉일 없는 41분 작품 → 자동 연결하지 않음
    a = C("특근", docid="K23900", prod_year="2016", use="인터넷용", runtime=36)
    b = C("특근", docid="K41252", prod_year="2026", use="극장용", runtime=41)
    assert decide("특근", None, [a, b], set()).status == "review"


def test_csv_loader_reads_columns_by_position(tmp_path) -> None:
    # 실제 '영화기본정보' CSV 모양: 헤더 25칸, 데이터 26칸(출연이 두 번), CP949.
    # 값은 손으로 만든 가짜
    head = ",".join(f"h{i}" for i in range(25))
    row = [
        "K",
        "00001",
        "가짜영화",
        "Fake",
        "Fake",
        "극영화",
        "극장용",
        "드라마",
        "대한민국",
        "2020",
    ]
    row += ["가짜사", "홍길동", "배우", "작가", "배우", "Y", "", "", "15세", "20200101", "100"]
    row += ["부산,바다", "부산에서 벌어지는 이야기", "http://x", "2020", "2021"]
    path = tmp_path / "kmdb.csv"
    with open(path, "w", encoding="cp949", newline="") as f:
        f.write(head + "\n")
        csv.writer(f).writerow(row)
    src = KmdbCsv.load(str(path))
    c = src.rows[0]
    assert (c.docid, c.title, c.use, c.country, c.prod_year, c.rel_date) == (
        "K00001",
        "가짜영화",
        "극장용",
        "대한민국",
        "2020",
        "20200101",
    )
    assert (c.runtime, c.director, c.keywords, c.plot) == (
        100,
        "홍길동",
        "부산,바다",
        "부산에서 벌어지는 이야기",
    )
    assert c.link == "https://www.kmdb.or.kr/db/kor/detail/movie/K/00001"

    assert [x.docid for x in asyncio.run(src.candidates("가짜 영화"))] == ["K00001"]
