"""한국어 자막 줄바꿈과 큐 묶기."""

from __future__ import annotations

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
