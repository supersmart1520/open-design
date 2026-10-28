"""미리보기 렌더 — 드래프트와 **같은** 컷·자막 데이터에서 나와야 한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from capcut_agent.draft import SubtitleStyle
from capcut_agent.framing import SHORTS, Canvas, crop_window
from capcut_agent.media import MediaInfo
from capcut_agent.render import (
    RenderPlan,
    ass_colour,
    ass_time,
    build_ass,
    build_command,
    crop_filter,
    font_px,
    select_expression,
    to_ass_xy,
)
from capcut_agent.silence import Range
from capcut_agent.subtitle import SubtitleCue
from capcut_agent.title import make_title


def _plan(tmp_path: Path, **overrides) -> RenderPlan:
    source = tmp_path / "a.mp4"
    source.write_bytes(b"x")
    media = MediaInfo(path=source, width=1920, height=1080, fps=30.0,
                      duration=20.0, has_audio=True)
    base = dict(
        media=media,
        keeps=[Range(0.0, 4.0), Range(6.0, 11.0)],
        cues=[SubtitleCue(0.0, 2.0, "첫 자막"), SubtitleCue(4.0, 6.0, "둘째 자막")],
        canvas=SHORTS,
        speech_style=SubtitleStyle.from_pixels(-850, 1920, 0, 1080),
    )
    base.update(overrides)
    return RenderPlan(**base)


# ------------------------------------------------------------------- 변환


def test_ass_colours_are_written_in_bgr():
    """ASS 는 BGR 순서다. 뒤집지 않으면 초록이 파랑으로 나온다."""
    assert ass_colour("#22C55E") == "&H005EC522"
    assert ass_colour("#FF0000") == "&H000000FF"
    assert ass_colour("#FFFFFF") == "&H00FFFFFF"


def test_a_bad_colour_is_rejected():
    with pytest.raises(ValueError):
        ass_colour("#FFF")


def test_times_are_formatted_for_ass():
    assert ass_time(0.0) == "0:00:00.00"
    assert ass_time(75.5) == "0:01:15.50"
    assert ass_time(-1.0) == "0:00:00.00"


def test_the_centre_maps_to_the_middle_of_the_canvas():
    assert to_ass_xy(0.0, 0.0, SHORTS) == (540, 960)


def test_a_negative_y_lands_near_the_bottom():
    _, y = to_ass_xy(0.0, -0.885, SHORTS)
    assert y > SHORTS.height * 0.9


def test_a_taller_canvas_scales_the_font_up():
    assert font_px(12, Canvas(2160, 3840)) > font_px(12, SHORTS)


# ----------------------------------------------------------------- 컷 적용


def test_the_select_expression_keeps_only_the_kept_ranges():
    expression = select_expression([Range(0.0, 4.0), Range(6.0, 11.0)])
    assert expression.count("between") == 2
    assert "0.000,4.000" in expression


def test_no_keeps_means_keep_everything():
    assert select_expression([]) == "1"


def test_a_landscape_source_gets_a_crop_in_a_vertical_canvas(tmp_path):
    plan = _plan(tmp_path)
    window = crop_window(plan.media.width, plan.media.height, SHORTS)
    assert crop_filter(window, plan.media).startswith("crop=")


def test_a_matching_aspect_needs_no_crop(tmp_path):
    plan = _plan(tmp_path, canvas=Canvas(1920, 1080))
    window = crop_window(plan.media.width, plan.media.height, Canvas(1920, 1080))
    assert crop_filter(window, plan.media) == ""


# -------------------------------------------------------------------- ASS


def test_every_cue_becomes_a_dialogue_line(tmp_path):
    ass = build_ass(_plan(tmp_path))
    assert ass.count("Dialogue:") == 2


def test_the_canvas_size_is_declared(tmp_path):
    ass = build_ass(_plan(tmp_path))
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


def test_our_line_breaks_survive(tmp_path):
    """줄바꿈은 이미 정했다. libass 가 다시 접으면 안 된다."""
    plan = _plan(tmp_path, cues=[SubtitleCue(0.0, 2.0, "첫 줄\n둘째 줄")])
    ass = build_ass(plan)
    assert r"\N" in ass
    assert "WrapStyle: 2" in ass


def test_a_title_adds_one_more_line(tmp_path):
    plan = _plan(
        tmp_path,
        title=make_title("학습코칭 선택한 이유", "green"),
        title_style=SubtitleStyle.from_pixels(0, 1920, 0, 1080, font_size=26),
        title_span=(0.0, 3.0),
    )
    assert build_ass(plan).count("Dialogue:") == 3


def test_the_title_lines_carry_different_colours(tmp_path):
    plan = _plan(
        tmp_path,
        title=make_title("학습코칭 선택한 이유", "green"),
        title_style=SubtitleStyle.from_pixels(0, 1920, 0, 1080, font_size=26),
        title_span=(0.0, 3.0),
    )
    ass = build_ass(plan)
    assert ass_colour("#22C55E") in ass
    assert ass_colour("#FFFFFF") in ass


def test_a_long_second_title_line_is_rendered_smaller(tmp_path):
    title = make_title("부모님 잔소리 아이한테 하지마세요", "red")
    plan = _plan(
        tmp_path, title=title,
        title_style=SubtitleStyle.from_pixels(0, 1920, 0, 1080, font_size=26),
        title_span=(0.0, 3.0),
    )
    ass = build_ass(plan)
    sizes = [int(part.split("\\")[0]) for part in ass.split("\\fs")[1:]]
    assert len(sizes) >= 2 and sizes[1] < sizes[0]


def test_the_title_is_drawn_above_the_speech_subtitles(tmp_path):
    plan = _plan(
        tmp_path, title=make_title("학습코칭 선택한 이유"),
        title_style=SubtitleStyle.from_pixels(0, 1920, 0, 1080, font_size=26),
        title_span=(0.0, 3.0),
    )
    lines = [l for l in build_ass(plan).splitlines() if l.startswith("Dialogue:")]
    layers = [int(l.split(":", 1)[1].split(",")[0]) for l in lines]
    assert max(layers) > min(layers)


# ---------------------------------------------------------------- 명령 조립


def test_the_command_applies_cuts_to_both_streams(tmp_path):
    command = build_command(_plan(tmp_path), tmp_path / "s.ass", tmp_path / "o.mp4")
    video = command[command.index("-vf") + 1]
    audio = command[command.index("-af") + 1]
    assert "select=" in video and "aselect=" in audio
    assert "setpts" in video and "asetpts" in audio


def test_the_command_scales_to_the_canvas(tmp_path):
    command = build_command(_plan(tmp_path), tmp_path / "s.ass", tmp_path / "o.mp4")
    assert "scale=1080:1920" in command[command.index("-vf") + 1]


def test_colons_in_the_subtitle_path_are_escaped(tmp_path):
    """이스케이프하지 않으면 필터 문법과 충돌해 ffmpeg 가 죽는다."""
    command = build_command(
        _plan(tmp_path), Path("/tmp/a:b/s.ass"), tmp_path / "o.mp4"
    )
    assert r"\:" in command[command.index("-vf") + 1]
