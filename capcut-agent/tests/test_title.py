"""제목 규칙 — 릴스 썸네일 문법.

두 줄, 명사형 종결, 행별 글자 수, 2행 축소, 2색 팔레트.
"""

from __future__ import annotations

import pytest

from capcut_agent.title import (
    EXPAND_THRESHOLD,
    LINE1_MAX,
    LINE1_MIN,
    LINE2_MAX,
    PALETTES,
    SHRINK,
    is_noun_ending,
    make_title,
    split_title,
    strip_particle,
)


# ------------------------------------------------------------ 두 줄 가르기


@pytest.mark.parametrize(
    "text, line1, line2",
    [
        ("학습코칭 선택한 이유", "학습코칭", "선택한 이유"),
        ("수능 D-30 국어 공부법", "수능 D-30", "국어 공부법"),
        ("부모님 잔소리 아이한테 하지마세요", "부모님 잔소리", "아이한테 하지마세요"),
        ("올해 고3/N수 정말 대단한 이유", "올해 고3/N수", "정말 대단한 이유"),
    ],
)
def test_reference_titles_split_the_way_the_samples_do(text, line1, line2):
    title = make_title(text)
    assert (title.line1, title.line2) == (line1, line2)


def test_an_explicit_line_break_is_respected():
    title = make_title("중학생 필독서\n추천도서 3종")
    assert title.line1 == "중학생 필독서"
    assert title.line2 == "추천도서 3종"


def test_a_single_word_becomes_one_line():
    title = make_title("학습코칭")
    assert title.line1 == "학습코칭"
    assert title.line2 == ""


def test_the_split_prefers_the_recommended_line_lengths():
    line1, line2 = split_title("초보 마케터 필수 툴 3가지")
    assert LINE1_MIN <= len(line1) <= LINE1_MAX + 2
    assert len(line2) <= LINE2_MAX + 2


# -------------------------------------------------------- 명사형 종결 규칙


@pytest.mark.parametrize("line", ["선택한 이유", "국어 공부법", "필수 공부 5가지",
                                  "추천도서 3종", "매출 3배 비결"])
def test_noun_endings_pass(line):
    assert is_noun_ending(line)


@pytest.mark.parametrize("line", ["공부를 합니다", "꼭 해보세요", "이렇게 하세요",
                                  "정말 좋아요"])
def test_verb_endings_are_flagged(line):
    """썸네일에서 동사 서술형은 길고 약하다."""
    assert not is_noun_ending(line)


def test_a_question_mark_does_not_hide_a_verb_ending():
    assert not is_noun_ending("이렇게 하세요?")


def test_a_verb_ending_shows_up_as_a_warning():
    title = make_title("공부법 지금 바로 해보세요")
    assert any("동사 서술형" in note for note in title.warnings)


def test_a_clean_title_has_no_warnings():
    assert make_title("학습코칭 선택한 이유").warnings == []


def test_trailing_particles_are_stripped_from_the_first_line():
    assert strip_particle("학습코칭을") == "학습코칭"
    assert strip_particle("공부의") == "공부"


def test_stripping_never_leaves_a_single_character():
    """'수의' 에서 조사를 떼면 '수' 한 글자만 남아 뜻이 사라진다."""
    assert strip_particle("수의") == "수의"


# ------------------------------------------------------------ 크기 계층


def test_two_short_lines_stay_the_same_size():
    title = make_title("학습코칭 선택한 이유")
    assert not title.shrink_line2
    assert title.line2_scale == 1.0


def test_a_long_second_line_is_shrunk():
    title = make_title("부모님 잔소리 아이한테 하지마세요")
    assert len(title.line2) >= EXPAND_THRESHOLD
    assert title.line2_scale == SHRINK


def test_a_second_line_much_longer_than_the_first_is_shrunk():
    title = make_title("맞팔 노가다 계정 망치는 지름길")
    assert title.line2_scale == SHRINK


def test_a_single_line_title_is_never_shrunk():
    assert make_title("학습코칭").line2_scale == 1.0


# -------------------------------------------------------------- 팔레트


def test_the_two_offered_palettes_are_green_and_red():
    assert set(PALETTES) == {"green", "red"}


def test_the_first_line_takes_the_accent_colour():
    assert make_title("학습코칭 선택한 이유", "green").palette.accent == "#22C55E"
    assert make_title("학습코칭 선택한 이유", "red").palette.accent == "#EF4444"


def test_the_second_line_is_white_in_both_palettes():
    for name in PALETTES:
        assert make_title("학습코칭 선택한 이유", name).palette.plain == "#FFFFFF"


def test_the_stroke_is_black_so_it_separates_from_any_clothing():
    for name in PALETTES:
        assert make_title("학습코칭 선택한 이유", name).palette.stroke == "#000000"


def test_an_unknown_palette_falls_back_rather_than_crashing():
    assert make_title("학습코칭 선택한 이유", "무지개").palette.name in PALETTES


# -------------------------------------------------------------- 정리 규칙


def test_wrapping_quotes_are_stripped():
    assert make_title('"학습코칭 선택한 이유"').line1 == "학습코칭"


def test_a_trailing_period_is_dropped():
    assert not make_title("학습코칭 선택한 이유.").line2.endswith(".")


def test_runs_of_whitespace_collapse():
    assert make_title("학습코칭   선택한  이유").line2 == "선택한 이유"


def test_an_empty_title_is_none():
    """빈 텍스트 세그먼트는 캡컷에서 빈 상자로 남는다."""
    assert make_title("") is None
    assert make_title("   ") is None
    assert make_title("...") is None


def test_an_over_long_title_is_trimmed_at_a_word_boundary():
    title = make_title("초보 마케터 알아두면 정말 쓸모 있는 필수 툴 10가지", max_chars=16)
    assert len(title.line1) + len(title.line2) <= 16
    assert "  " not in title.text


def test_the_rendered_text_is_two_lines():
    assert make_title("학습코칭 선택한 이유").text == "학습코칭\n선택한 이유"
