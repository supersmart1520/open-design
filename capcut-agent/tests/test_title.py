"""제목 규칙 — 영상당 한 장, 한 줄, 짧게."""

from __future__ import annotations

from capcut_agent.title import DEFAULT_MAX_CHARS, clean_title


def test_a_short_title_passes_through():
    assert clean_title("왜 대부분이 실패할까요?") == "왜 대부분이 실패할까요?"


def test_line_breaks_and_runs_of_spaces_collapse():
    assert clean_title("여러   공백과\n줄바꿈") == "여러 공백과 줄바꿈"


def test_a_trailing_period_is_dropped():
    assert clean_title("순서가 가장 중요합니다.") == "순서가 가장 중요합니다"


def test_a_question_mark_survives():
    """물음표는 후킹이라 남긴다."""
    assert clean_title("정말 그럴까요?").endswith("?")


def test_an_exclamation_mark_survives():
    assert clean_title("이건 꼭 보세요!").endswith("!")


def test_wrapping_quotes_are_stripped():
    assert clean_title('"이것만 알면 됩니다"') == "이것만 알면 됩니다"


def test_wrapping_brackets_are_stripped():
    assert clean_title("(괄호로 감쌈)") == "괄호로 감쌈"


def test_nested_wrappers_are_stripped():
    assert clean_title('“(이중으로 감쌈)”') == "이중으로 감쌈"


def test_a_long_title_is_cut_at_a_word_boundary():
    title = clean_title("테슬라 공중부양 영상 만들기 전 무조건 알고 가세요", max_chars=16)
    assert len(title) <= 16
    assert title.endswith("…")
    # 어절 중간에서 잘리지 않았는지.
    assert not title[:-1].rstrip().endswith(("공중부", "만들"))


def test_the_default_budget_is_short_enough_to_scan():
    assert DEFAULT_MAX_CHARS <= 24


def test_a_single_long_word_is_cut_by_characters():
    title = clean_title("정말" * 20, max_chars=10)
    assert len(title) <= 10


def test_an_empty_title_stays_empty():
    """빈 텍스트 세그먼트는 캡컷에서 빈 상자로 남는다."""
    assert clean_title("") == ""
    assert clean_title("   ") == ""
    assert clean_title('""') == ""
    assert clean_title("...") == ""


def test_punctuation_only_titles_are_dropped():
    assert clean_title("…") == ""
    assert clean_title("-- ") == ""


def test_a_tiny_budget_does_not_crash():
    assert len(clean_title("아주 긴 제목입니다", max_chars=3)) <= 3
