"""무음 → 보존 구간 로직. ffmpeg 없이 stderr 원문만으로 검증한다."""

from __future__ import annotations

import pytest

from capcut_agent.silence import (
    Range,
    SilenceOptions,
    invert,
    keeps_from_silence,
    merge,
    pad_and_tidy,
    parse_silence,
)

SAMPLE = """
[silencedetect @ 0x1] silence_start: 1.5
[silencedetect @ 0x1] silence_end: 2.4 | silence_duration: 0.9
[silencedetect @ 0x1] silence_start: 5.0
[silencedetect @ 0x1] silence_end: 6.2 | silence_duration: 1.2
"""


def test_parse_silence_pairs_start_and_end():
    assert parse_silence(SAMPLE, duration=10.0) == [Range(1.5, 2.4), Range(5.0, 6.2)]


def test_parse_silence_closes_trailing_silence_at_duration():
    text = SAMPLE + "[silencedetect @ 0x1] silence_start: 9.0\n"
    assert parse_silence(text, duration=10.0)[-1] == Range(9.0, 10.0)


def test_parse_silence_clamps_end_to_duration():
    assert parse_silence("silence_start: 1.0\nsilence_end: 99.0\n", 10.0) == [Range(1.0, 10.0)]


def test_merge_joins_ranges_within_tolerance():
    assert merge([Range(0, 1), Range(1.05, 2)], tolerance=0.1) == [Range(0, 2)]


def test_merge_keeps_ranges_apart_beyond_tolerance():
    assert merge([Range(0, 1), Range(1.5, 2)], tolerance=0.1) == [Range(0, 1), Range(1.5, 2)]


def test_invert_produces_complement():
    keeps = invert([Range(1.5, 2.4), Range(5.0, 6.2)], duration=10.0)
    assert keeps == [Range(0, 1.5), Range(2.4, 5.0), Range(6.2, 10.0)]


def test_invert_handles_silence_at_both_edges():
    assert invert([Range(0, 1.0), Range(9.0, 10.0)], 10.0) == [Range(1.0, 9.0)]


def test_pad_never_crosses_into_a_neighbour():
    opts = SilenceOptions(padding=1.0, min_keep=0.0, min_gap=0.0)
    keeps = pad_and_tidy([Range(0.0, 1.0), Range(1.4, 3.0)], duration=3.0, opts=opts)
    # 간격이 0.4s 뿐이라 각자 0.2s 씩만 가져가고, 그러면 맞닿아 하나로 합쳐진다.
    assert keeps == [Range(0.0, 3.0)]


def test_min_keep_drops_slivers():
    opts = SilenceOptions(padding=0.0, min_keep=0.5, min_gap=0.0)
    assert pad_and_tidy([Range(0.0, 0.2), Range(1.0, 3.0)], 3.0, opts) == [Range(1.0, 3.0)]


def test_min_gap_merges_nearby_keeps_to_reduce_cuts():
    opts = SilenceOptions(padding=0.0, min_keep=0.0, min_gap=0.5)
    assert pad_and_tidy([Range(0.0, 1.0), Range(1.3, 2.0)], 2.0, opts) == [Range(0.0, 2.0)]


def test_end_to_end_from_ffmpeg_stderr():
    opts = SilenceOptions(padding=0.05, min_keep=0.3, min_gap=0.1)
    keeps = keeps_from_silence(SAMPLE, duration=10.0, opts=opts)
    assert len(keeps) == 3
    assert keeps[0].start == 0.0
    assert keeps[-1].end == 10.0
    assert 7.9 < sum(k.length for k in keeps) < 8.3


def test_no_silence_keeps_whole_clip():
    assert keeps_from_silence("", 12.0, SilenceOptions()) == [Range(0.0, 12.0)]


# ------------------------------------------- 대본 기준 컷 (whisper 단어)


class _W:
    """Word 최소 구현 — keeps_from_words 는 start/end 만 본다."""

    def __init__(self, start: float, end: float):
        self.start = start
        self.end = end


def test_keeps_from_words_cuts_only_between_words():
    from capcut_agent.silence import keeps_from_words

    words = [_W(0.0, 1.0), _W(1.1, 2.0), _W(5.0, 6.0)]
    opts = SilenceOptions(min_silence=0.35, padding=0.0, min_keep=0.0, min_gap=0.0)
    keeps = keeps_from_words(words, duration=8.0, opts=opts)
    # 0.1초 틈은 붙고, 3초 틈에서만 잘린다.
    assert keeps == [Range(0.0, 2.0), Range(5.0, 6.0)]


def test_no_word_ever_straddles_a_cut_boundary():
    """이게 보장되면 '말은 있는데 자막이 없는 구간' 이 생기지 않는다."""
    from capcut_agent.silence import keeps_from_words, overlap_to_timeline

    words = [_W(0.0, 1.0), _W(1.1, 2.0), _W(5.0, 6.0), _W(6.05, 7.0)]
    opts = SilenceOptions(min_silence=0.35, padding=0.08, min_keep=0.0, min_gap=0.0)
    keeps = keeps_from_words(words, duration=9.0, opts=opts)
    for word in words:
        assert overlap_to_timeline(keeps, word.start, word.end) is not None


def test_keeps_from_words_with_no_words_is_empty():
    from capcut_agent.silence import keeps_from_words

    assert keeps_from_words([], 10.0, SilenceOptions()) == []


def test_zero_length_words_are_ignored():
    from capcut_agent.silence import keeps_from_words

    assert keeps_from_words([_W(1.0, 1.0)], 10.0, SilenceOptions()) == []


# ------------------------------------------------- 컷 경계에 걸친 단어


def test_a_word_straddling_a_cut_keeps_its_surviving_part():
    """버리면 자막이 사라진다. 겹치는 부분만 잘라 쓴다."""
    from capcut_agent.silence import overlap_to_timeline

    keeps = [Range(0.0, 2.0), Range(5.0, 8.0)]
    # 1.8~2.3 은 앞 구간에 0.2초만 걸친다.
    span = overlap_to_timeline(keeps, 1.8, 2.3)
    assert span is not None
    start, end = span
    assert start == pytest.approx(1.8)
    assert end == pytest.approx(2.0)


def test_a_word_entirely_inside_a_cut_is_dropped():
    from capcut_agent.silence import overlap_to_timeline

    assert overlap_to_timeline([Range(0.0, 2.0), Range(5.0, 8.0)], 3.0, 4.0) is None


def test_a_word_spanning_two_keeps_maps_across_the_join():
    from capcut_agent.silence import overlap_to_timeline

    keeps = [Range(0.0, 2.0), Range(5.0, 8.0)]
    span = overlap_to_timeline(keeps, 1.5, 5.5)
    assert span is not None
    start, end = span
    assert start == pytest.approx(1.5)
    # 앞 구간 2초 + 뒤 구간 0.5초
    assert end == pytest.approx(2.5)


def test_a_zero_length_word_inside_a_keep_survives():
    from capcut_agent.silence import overlap_to_timeline

    assert overlap_to_timeline([Range(0.0, 2.0)], 1.0, 1.0) == (1.0, 1.0)


# ------------------------------------------- 합침 (음량 + 단어 경계)


def _silence_stderr(*pairs) -> str:
    return "".join(f"silence_start: {a}\nsilence_end: {b}\n" for a, b in pairs)


def test_hybrid_cuts_as_often_as_the_volume_detector():
    """대본 기준은 whisper 의 늘어난 단어 끝 때문에 짧은 틈을 놓친다."""
    from capcut_agent.silence import keeps_from_words, keeps_hybrid

    stderr = _silence_stderr((0.0, 0.5), (3.0, 3.6), (6.0, 8.5), (11.0, 11.6))
    # whisper 는 덩어리 마지막 단어의 끝을 다음 쪽으로 늘려 잡는다. 그래서
    # 0.6초짜리 틈이 0.3초로 보이고 min_silence(0.35)에 안 걸린다.
    words = [
        _W(0.5, 2.4), _W(2.4, 3.3),
        _W(3.6, 5.5), _W(5.5, 6.3),
        _W(8.5, 10.4), _W(10.4, 11.3),
        _W(11.6, 14.0),
    ]
    opts = SilenceOptions(padding=0.08, min_keep=0.3, min_gap=0.12)
    hybrid = keeps_hybrid(stderr, words, 16.0, opts)
    speech_only = keeps_from_words(words, 16.0, opts)
    assert len(hybrid) > len(speech_only)


def test_hybrid_never_slices_a_word():
    """이게 깨지면 말은 남았는데 자막이 없는 구간이 생긴다."""
    from capcut_agent.silence import keeps_hybrid, overlap_to_timeline

    stderr = _silence_stderr((3.0, 3.6), (11.0, 11.6))
    # 경계 바로 바깥까지 늘어난 단어들.
    words = [_W(0.5, 3.3), _W(3.6, 6.0), _W(8.5, 11.3), _W(11.6, 14.0)]
    keeps = keeps_hybrid(stderr, words, 16.0, SilenceOptions())
    for word in words:
        assert any(
            word.start >= keep.start - 1e-6 and word.end <= keep.end + 1e-6
            for keep in keeps
        ), f"{word.start}~{word.end} 가 잘렸습니다"


def test_snapping_widens_only_the_overlapping_keep():
    from capcut_agent.silence import snap_to_words

    keeps = [Range(0.0, 2.0), Range(5.0, 8.0)]
    snapped = snap_to_words(keeps, [_W(1.5, 2.4)], 10.0)
    assert snapped[0] == Range(0.0, 2.4)
    assert snapped[1] == Range(5.0, 8.0)


def test_snapping_merges_keeps_that_grow_into_each_other():
    from capcut_agent.silence import snap_to_words

    snapped = snap_to_words([Range(0.0, 2.0), Range(2.2, 4.0)], [_W(1.8, 2.3)], 10.0)
    assert snapped == [Range(0.0, 4.0)]


def test_snapping_without_words_changes_nothing():
    from capcut_agent.silence import snap_to_words

    keeps = [Range(0.0, 2.0)]
    assert snap_to_words(keeps, [], 10.0) == keeps


def test_snapping_stays_inside_the_clip():
    from capcut_agent.silence import snap_to_words

    snapped = snap_to_words([Range(8.0, 10.0)], [_W(9.0, 99.0)], 10.0)
    assert snapped[0].end <= 10.0
