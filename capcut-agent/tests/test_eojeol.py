"""어절 복원 — whisper 가 쪼갠 토막을 단어+조사 단위로 되돌린다."""

from __future__ import annotations

import pytest

from capcut_agent.eojeol import MIN_DURATION, merge_eojeol
from capcut_agent.subtitle import Word
from capcut_agent.transcribe import normalize


def _texts(words: list[Word]) -> list[str]:
    return [w.text for w in words]


# ------------------------------------------------------------------ 어절 경계


def test_a_particle_without_leading_space_joins_the_previous_word():
    out = merge_eojeol([Word(" 순서", 0.0, 0.4), Word("가", 0.4, 0.5)])
    assert _texts(out) == ["순서가"]


def test_a_leading_space_starts_a_new_eojeol():
    out = merge_eojeol([Word(" 순서", 0.0, 0.4), Word(" 가장", 0.5, 0.9)])
    assert _texts(out) == ["순서", "가장"]


def test_sentencepiece_underscore_counts_as_a_leading_space():
    out = merge_eojeol([Word("▁순서", 0.0, 0.4), Word("▁가장", 0.5, 0.9)])
    assert _texts(out) == ["순서", "가장"]


def test_a_split_verb_ending_is_rejoined():
    out = merge_eojeol([Word(" 중요합니", 0.9, 1.4), Word("다", 1.4, 1.5)])
    assert _texts(out) == ["중요합니다"]


def test_the_first_token_starts_an_eojeol_even_without_a_space():
    out = merge_eojeol([Word("순서", 0.0, 0.4), Word("가", 0.4, 0.5)])
    assert _texts(out) == ["순서가"]


def test_multiple_leading_spaces_are_stripped():
    assert _texts(merge_eojeol([Word("   순서", 0.0, 0.4)])) == ["순서"]


# -------------------------------------------------------------------- 구두점


def test_a_trailing_period_is_absorbed_by_the_previous_eojeol():
    out = merge_eojeol([Word(" 중요합니다", 0.9, 1.4), Word(".", 1.4, 1.4)])
    assert _texts(out) == ["중요합니다."]


def test_an_opening_quote_starts_the_next_eojeol():
    """여는 부호의 선행 공백은 다음 어절의 시작 신호로 이어져야 한다."""
    out = merge_eojeol([
        Word(" 말했다", 0.0, 0.5),
        Word(' "', 0.6, 0.6), Word("이것", 0.6, 0.9), Word("을", 0.9, 1.0),
    ])
    assert _texts(out) == ["말했다", '"이것을']


def test_punctuation_is_never_its_own_eojeol():
    out = merge_eojeol([Word(" 하나", 0.0, 0.4), Word(",", 0.4, 0.4), Word(" 둘", 0.5, 0.8)])
    assert all(len(w.text.strip(",.!?\"'")) > 0 for w in out)


def test_punctuation_is_never_dropped():
    out = merge_eojeol([Word(" 정말", 0.0, 0.4), Word("?!", 0.4, 0.4)])
    assert "".join(_texts(out)) == "정말?!"


def test_trailing_punctuation_with_no_following_word_is_kept():
    out = merge_eojeol([Word(" 끝", 0.0, 0.4), Word(' "', 0.5, 0.5)])
    assert "".join(_texts(out)).endswith('"')


# ----------------------------------------------------------------- 특수 토큰


def test_bracket_special_tokens_are_skipped():
    out = merge_eojeol([Word("[_BEG_]", 0.0, 0.0), Word(" 안녕", 0.1, 0.5)])
    assert _texts(out) == ["안녕"]


def test_angle_special_tokens_are_skipped():
    out = merge_eojeol([Word("<|endoftext|>", 0.0, 0.0), Word(" 안녕", 0.1, 0.5)])
    assert _texts(out) == ["안녕"]


def test_blank_tokens_are_skipped():
    assert merge_eojeol([Word("   ", 0.0, 0.2), Word("\t", 0.2, 0.3)]) == []


# ------------------------------------------------------------------- 불변식


def test_start_times_are_strictly_monotonic_even_with_bad_offsets():
    # 비단조 offset 이 섞여 와도 순서가 뒤집히지 않아야 한다.
    out = merge_eojeol([
        Word(" 하나", 1.0, 1.4), Word(" 둘", 0.5, 0.9), Word(" 셋", 0.5, 0.9),
    ])
    starts = [w.start for w in out]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


def test_every_eojeol_has_positive_duration():
    out = merge_eojeol([Word(" 하나", 1.0, 1.0), Word(" 둘", 1.0, 1.0)])
    assert all(w.end > w.start for w in out)


def test_a_zero_width_token_gets_the_minimum_duration():
    out = merge_eojeol([Word(" 하나", 2.0, 2.0)])
    assert out[0].end - out[0].start == pytest.approx(MIN_DURATION)


def test_an_empty_input_gives_an_empty_result():
    assert merge_eojeol([]) == []


def test_the_pass_is_idempotent():
    """이미 어절인 입력을 다시 통과시켜도 달라지지 않아야 한다."""
    once = merge_eojeol([Word(" 순서", 0.0, 0.4), Word("가", 0.4, 0.5), Word(" 가장", 0.5, 0.9)])
    twice = merge_eojeol([Word(f" {w.text}", w.start, w.end) for w in once])
    assert _texts(once) == _texts(twice)


# ----------------------------------------------------- 전사 파이프라인 연결


def test_normalize_reassembles_eojeol_from_whisper_output():
    raw = {
        "language": "ko",
        "segments": [{
            "text": " 순서가 가장 중요합니다.",
            "start": 0.0, "end": 1.5,
            "words": [
                {"word": " 순서", "start": 0.0, "end": 0.4},
                {"word": "가", "start": 0.4, "end": 0.5},
                {"word": " 가장", "start": 0.5, "end": 0.9},
                {"word": " 중요합니", "start": 0.9, "end": 1.4},
                {"word": "다", "start": 1.4, "end": 1.5},
            ],
        }],
    }
    transcript = normalize(raw, "large-v3", "ko")
    assert _texts(transcript.words) == ["순서가", "가장", "중요합니다"]


def test_normalize_handles_the_text_key_as_well_as_word():
    """mlx-whisper 와 faster-whisper 의 키 이름 차이."""
    raw = {"segments": [{
        "text": "안녕", "start": 0.0, "end": 0.5,
        "words": [{"text": " 안녕", "start": 0.0, "end": 0.5}],
    }]}
    assert _texts(normalize(raw, "m", "ko").words) == ["안녕"]


def test_normalize_survives_a_transcript_with_no_word_timestamps():
    raw = {"segments": [{"text": "안녕하세요", "start": 0.0, "end": 1.0}]}
    transcript = normalize(raw, "m", "ko")
    assert transcript.words == []
    assert len(transcript.segments) == 1
