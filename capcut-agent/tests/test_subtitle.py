"""한국어 자막 줄바꿈과 큐 묶기."""

from __future__ import annotations

import pytest

from capcut_agent.subtitle import (
    BOUND_NOUNS,
    SubtitleCue,
    Word,
    cues_from_words,
    is_bound_noun,
    split_text,
    to_srt,
    wrap,
)


def test_bound_nouns_are_recognised_with_and_without_particles():
    assert is_bound_noun("수")
    assert is_bound_noun("수가")
    assert is_bound_noun("것을")
    assert is_bound_noun("때문에")


def test_ordinary_nouns_that_merely_start_alike_are_not_bound():
    # 이게 틀리면 멀쩡한 어절을 끌어내려 줄이 이상해진다.
    assert not is_bound_noun("데이터")
    assert not is_bound_noun("거리")
    assert not is_bound_noun("수요일")
    assert not is_bound_noun("등산")


def test_every_listed_bound_noun_is_recognised_bare():
    for noun in BOUND_NOUNS:
        assert is_bound_noun(noun), noun


def test_wrap_respects_the_character_budget():
    lines = wrap("가나다라마바사아자차카타파하가나다라마바사", max_chars=10)
    assert all(len(line) <= 10 for line in lines)


def test_wrap_never_starts_a_line_with_a_bound_noun():
    # 끌어내리기가 동작하지 않으면 "수 있습니다" 가 둘째 줄을 시작한다.
    lines = wrap("우리가 이것을 전부 할 수 있습니다", max_chars=12)
    assert len(lines) >= 2
    for line in lines[1:]:
        assert not is_bound_noun(line.split()[0]), lines


def test_pulling_down_keeps_the_previous_line_non_empty():
    # 앞 줄이 어절 하나뿐이면 끌어내릴 수 없다. 터지지 않고 그냥 둬야 한다.
    lines = wrap("아주아주아주긴단어하나 수가", max_chars=12)
    assert all(line.strip() for line in lines)


def test_wrap_of_empty_text_is_empty():
    assert wrap("") == []
    assert wrap("   ") == []


def test_split_text_chunks_beyond_the_line_limit():
    chunks = split_text(" ".join(["단어"] * 40), max_chars=12, max_lines=2)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.split("\n")) <= 2


def _words(pairs: list[tuple[str, float, float]]) -> list[Word]:
    return [Word(text, start, end) for text, start, end in pairs]


def test_cues_break_on_a_pause():
    words = _words([("안녕하세요", 0.0, 0.8), ("반갑습니다", 2.0, 2.9)])
    cues = cues_from_words(words, gap_break=0.5)
    assert len(cues) == 2


def test_cues_do_not_break_without_a_pause():
    words = _words([("안녕", 0.0, 0.4), ("하세요", 0.45, 0.9)])
    assert len(cues_from_words(words, gap_break=0.5)) == 1


def test_cues_break_after_sentence_punctuation():
    words = _words([("끝입니다.", 0.0, 0.8), ("다음", 0.9, 1.2)])
    assert len(cues_from_words(words)) == 2


def test_cues_break_when_the_budget_fills():
    words = _words([(f"단어{i}", i * 0.3, i * 0.3 + 0.25) for i in range(20)])
    cues = cues_from_words(words, max_chars=10, max_lines=2)
    assert len(cues) > 1


def test_cue_times_stay_inside_the_spoken_span():
    words = _words([("하나", 1.0, 1.4), ("둘", 1.5, 1.9)])
    cue = cues_from_words(words)[0]
    assert cue.start >= 1.0
    assert cue.end <= 1.9 + 1e-6


def test_cues_are_ordered_and_non_overlapping():
    words = _words([(f"어절{i}", i * 1.0, i * 1.0 + 0.6) for i in range(8)])
    cues = cues_from_words(words, max_chars=8, max_lines=1, gap_break=0.3)
    for earlier, later in zip(cues, cues[1:]):
        assert earlier.end <= later.start + 1e-6


def test_blank_words_are_ignored():
    words = _words([("   ", 0.0, 0.2), ("말", 0.3, 0.6)])
    cues = cues_from_words(words)
    assert len(cues) == 1
    assert cues[0].text == "말"


def test_cue_exposes_its_lines():
    assert SubtitleCue(0.0, 1.0, "첫째\n둘째").lines == ["첫째", "둘째"]


def test_srt_export_is_well_formed():
    srt = to_srt([SubtitleCue(1.5, 3.25, "안녕하세요")])
    assert "00:00:01,500 --> 00:00:03,250" in srt
    assert "안녕하세요" in srt
    assert srt.startswith("1\n")


def test_a_token_longer_than_the_line_is_hard_split():
    # 쪼개지 않으면 캡컷에서 넘쳐 잘린다. 쪼개는 쪽이 덜 나쁘다.
    lines = wrap("가나다라마바사아자차카타파하", max_chars=6)
    assert all(len(line) <= 6 for line in lines)
    assert "".join(lines).replace(" ", "") == "가나다라마바사아자차카타파하"


def test_normal_words_are_never_split_mid_word():
    lines = wrap("우리가 이것을 해봅시다", max_chars=8)
    for line in lines:
        for token in line.split():
            assert token in {"우리가", "이것을", "해봅시다"}


# -------------------------------------------- 끊는 기준 (실사용 버그)


def test_a_korean_sentence_ending_breaks_the_cue_without_punctuation():
    """whisper 는 구두점을 자주 빠뜨린다. 종결어미로도 끊어야 한다."""
    words = _words([
        ("오늘", 0.0, 0.4), ("세", 0.4, 0.7), ("가지입니다", 0.7, 1.4),
        ("첫째", 1.5, 1.9), ("기준입니다", 1.9, 2.6),
    ])
    cues = cues_from_words(words, max_chars=40, gap_break=5.0)
    assert len(cues) == 2
    assert cues[0].text.endswith("가지입니다")


def test_ordinary_words_do_not_look_like_sentence_endings():
    from capcut_agent.subtitle import _SENTENCE_END

    for token in ("먼저", "그래서", "가장", "세", "기준을"):
        assert not _SENTENCE_END.search(token), token


def test_a_cue_never_outstays_the_duration_cap():
    from capcut_agent.subtitle import DEFAULT_MAX_DURATION

    # 종결어미도 쉼도 없이 길게 이어지는 경우.
    words = _words([(f"말{i}", i * 1.0, i * 1.0 + 0.9) for i in range(12)])
    cues = cues_from_words(words, max_chars=200, gap_break=5.0)
    for cue in cues:
        assert cue.duration <= DEFAULT_MAX_DURATION + 1.0


def test_the_duration_cap_is_a_safety_net_not_a_scissors():
    """상한이 낮으면 문장 한가운데를 자른다. 6초 문장은 통째로 남아야 한다."""
    words = _words([
        ("첫째", 0.0, 1.2), ("기준을", 1.3, 2.5),
        ("먼저", 2.6, 3.8), ("정해야", 3.9, 4.6), ("합니다", 4.7, 5.4),
    ])
    cues = cues_from_words(words, max_chars=40, gap_break=5.0)
    assert len(cues) == 1
    assert cues[0].text.endswith("합니다")


def test_a_cue_never_spans_a_cut_boundary():
    """이번 버그의 핵심.

    컷으로 무음을 걷어내면 멀리 있던 두 문장이 타임라인에서 맞붙는다.
    틈이 사라져 '쉼' 신호가 안 걸리므로, 컷 경계를 따로 알려줘야 한다.
    """
    words = _words([
        ("앞문장", 0.0, 1.0), ("입니다", 1.0, 1.8),
        ("뒷문장", 1.8, 2.6), ("이어짐", 2.6, 3.4),
    ])
    without = cues_from_words(words, max_chars=200, gap_break=5.0)
    with_cut = cues_from_words(
        words, max_chars=200, gap_break=5.0, boundaries=[1.8]
    )
    assert len(with_cut) > len(without) or len(with_cut) >= 2
    for cue in with_cut:
        assert not (cue.start < 1.8 < cue.end), "자막이 컷 경계를 넘었습니다"


def test_boundaries_outside_the_speech_change_nothing():
    words = _words([("하나", 0.0, 0.5), ("둘입니다", 0.5, 1.0)])
    assert cues_from_words(words, boundaries=[99.0]) == cues_from_words(words)


# ----------------------------------------- 영상 크기에 맞춘 줄바꿈 (Capite)


def test_a_bigger_font_fits_fewer_characters_per_line():
    from capcut_agent.subtitle import auto_max_chars

    assert auto_max_chars(20) < auto_max_chars(12) < auto_max_chars(8)


def test_the_default_font_keeps_the_hand_tuned_line_length():
    from capcut_agent.subtitle import DEFAULT_MAX_CHARS, auto_max_chars

    assert auto_max_chars(12) == DEFAULT_MAX_CHARS


def test_latin_fits_more_characters_than_korean():
    from capcut_agent.subtitle import (
        CHAR_WIDTH_RATIO_LATIN,
        auto_max_chars,
    )

    latin = auto_max_chars(12, char_width_ratio=CHAR_WIDTH_RATIO_LATIN)
    assert latin > auto_max_chars(12)


def test_a_narrower_line_fits_fewer_characters():
    from capcut_agent.subtitle import auto_max_chars

    assert auto_max_chars(12, line_max_width=0.5) < auto_max_chars(12)


def test_absurd_font_sizes_do_not_blow_up():
    from capcut_agent.subtitle import auto_max_chars

    assert auto_max_chars(0) >= 6
    assert auto_max_chars(1000) >= 6


# --------------------------------------------------- 자막 사이 빈틈 메우기


def test_a_cue_is_held_until_the_next_one_appears():
    from capcut_agent.subtitle import fill_gaps

    filled = fill_gaps([SubtitleCue(0.0, 1.0, "하나"), SubtitleCue(2.5, 3.5, "둘")])
    assert filled[0].end == pytest.approx(2.5)


def test_the_last_cue_runs_to_the_end_of_the_timeline():
    from capcut_agent.subtitle import fill_gaps

    filled = fill_gaps([SubtitleCue(0.0, 1.0, "끝")], until=3.0)
    assert filled[0].end == pytest.approx(3.0)


def test_a_cue_is_never_held_across_a_cut():
    """컷을 넘겨 붙들면 앞 문장이 새 장면 위에 남는다."""
    from capcut_agent.subtitle import fill_gaps

    filled = fill_gaps(
        [SubtitleCue(0.0, 1.0, "앞"), SubtitleCue(4.0, 5.0, "뒤")],
        boundaries=[2.0],
    )
    assert filled[0].end == pytest.approx(2.0)


def test_a_long_silence_is_not_held_forever():
    from capcut_agent.subtitle import fill_gaps

    filled = fill_gaps(
        [SubtitleCue(0.0, 1.0, "하나"), SubtitleCue(30.0, 31.0, "둘")],
        max_hold=3.0,
    )
    assert filled[0].end == pytest.approx(4.0)


def test_filling_never_shortens_a_cue():
    from capcut_agent.subtitle import fill_gaps

    original = [SubtitleCue(0.0, 2.0, "길다"), SubtitleCue(1.5, 3.0, "겹침")]
    for before, after in zip(original, fill_gaps(original)):
        assert after.end >= before.end


def test_filling_an_empty_list_is_empty():
    from capcut_agent.subtitle import fill_gaps

    assert fill_gaps([]) == []


def test_a_tiny_gap_is_closed_by_pulling_the_next_cue_back():
    """컷 직후 패딩 때문에 두세 프레임이 비어 깜빡인다."""
    from capcut_agent.subtitle import fill_gaps

    filled = fill_gaps(
        [SubtitleCue(0.0, 2.0, "앞"), SubtitleCue(2.08, 4.0, "뒤")],
        boundaries=[2.0],
    )
    assert filled[1].start == pytest.approx(2.0)
    assert filled[0].end == pytest.approx(filled[1].start)


def test_a_real_pause_is_not_closed_by_snapping():
    from capcut_agent.subtitle import fill_gaps

    filled = fill_gaps(
        [SubtitleCue(0.0, 1.0, "앞"), SubtitleCue(10.0, 11.0, "뒤")],
        boundaries=[1.0], max_hold=3.0, snap=0.35,
    )
    assert filled[1].start == pytest.approx(10.0)
