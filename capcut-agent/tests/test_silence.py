"""무음 → 보존 구간 로직. ffmpeg 없이 stderr 원문만으로 검증한다."""

from __future__ import annotations

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
