"""숏폼 드래프트 — 9:16 캔버스, 크롭, 자막 트랙, 오디오 트랙, 다중 클립."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capcut_agent.draft import US, DraftBuilder, SubtitleStyle, hex_to_rgb
from capcut_agent.framing import SHORTS, Canvas
from capcut_agent.media import MediaInfo
from capcut_agent.silence import Range
from capcut_agent.subtitle import SubtitleCue


def _media(tmp_path: Path, name: str = "talk.mp4", width: int = 1920,
           height: int = 1080, duration: float = 60.0) -> MediaInfo:
    source = tmp_path / name
    source.write_bytes(b"fake")
    return MediaInfo(path=source, width=width, height=height, fps=30.0,
                     duration=duration, has_audio=True)


def _timeline(folder: Path) -> dict:
    return json.loads((folder / "draft_content.json").read_text("utf-8"))


# ------------------------------------------------------------------ 캔버스/크롭


def test_shorts_canvas_is_written_as_nine_sixteen(tmp_path):
    builder = DraftBuilder(media=_media(tmp_path), name="숏폼", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 5.0)])
    config = builder.timeline_json()["canvas_config"]
    assert (config["width"], config["height"]) == (1080, 1920)
    assert config["ratio"] == "9:16"


def test_landscape_source_gets_a_side_crop_in_a_shorts_canvas(tmp_path):
    builder = DraftBuilder(media=_media(tmp_path), name="숏폼", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 5.0)])
    material = builder.timeline_json()["materials"]["videos"][0]
    assert material["crop_ratio"] == "9:16"
    assert material["crop"]["upper_left_x"] == pytest.approx(0.341796875)
    assert material["crop"]["upper_right_x"] == pytest.approx(0.658203125)
    # 세로는 통째로 쓴다.
    assert material["crop"]["upper_left_y"] == 0.0
    assert material["crop"]["lower_left_y"] == 1.0


def test_crop_anchor_reaches_the_material(tmp_path):
    builder = DraftBuilder(media=_media(tmp_path), name="숏폼",
                           canvas=SHORTS, crop_anchor="left")
    builder.add_video_cuts([Range(0.0, 5.0)])
    crop = builder.timeline_json()["materials"]["videos"][0]["crop"]
    assert crop["upper_left_x"] == 0.0


def test_omitting_the_canvas_keeps_the_source_resolution(tmp_path):
    """기존 1단 동작. 가로 강의 영상을 가로 그대로 자를 때."""
    builder = DraftBuilder(media=_media(tmp_path), name="그대로")
    builder.add_video_cuts([Range(0.0, 5.0)])
    config = builder.timeline_json()["canvas_config"]
    assert (config["width"], config["height"]) == (1920, 1080)
    assert config["ratio"] == "original"


def test_a_vertical_source_in_a_shorts_canvas_is_not_cropped(tmp_path):
    builder = DraftBuilder(
        media=_media(tmp_path, width=1080, height=1920), name="세로원본", canvas=SHORTS
    )
    builder.add_video_cuts([Range(0.0, 5.0)])
    crop = builder.timeline_json()["materials"]["videos"][0]["crop"]
    assert crop["upper_left_x"] == 0.0 and crop["upper_right_x"] == 1.0


def test_builder_needs_a_canvas_or_a_media():
    with pytest.raises(ValueError):
        DraftBuilder(name="빈것")


# -------------------------------------------------------------------- 다중 클립


def test_several_clips_of_one_file_share_a_single_material(tmp_path):
    media = _media(tmp_path)
    builder = DraftBuilder(media=media, name="하이라이트", canvas=SHORTS)
    builder.add_clip(media, 5.0, 3.0)
    builder.add_clip(media, 20.0, 4.0)
    timeline = builder.timeline_json()
    assert len(timeline["materials"]["videos"]) == 1
    assert len(timeline["tracks"][0]["segments"]) == 2


def test_broll_from_different_files_gets_one_material_each(tmp_path):
    first = _media(tmp_path, "broll-a.mp4")
    second = _media(tmp_path, "broll-b.mp4")
    builder = DraftBuilder(name="보이스", canvas=SHORTS)
    builder.add_clip(first, 0.0, 3.0)
    builder.add_clip(second, 0.0, 4.0)
    timeline = builder.timeline_json()
    assert len(timeline["materials"]["videos"]) == 2
    paths = {Path(m["path"]).name for m in timeline["materials"]["videos"]}
    assert paths == {"broll-a.mp4", "broll-b.mp4"}


def test_clips_lay_down_back_to_back(tmp_path):
    media = _media(tmp_path)
    builder = DraftBuilder(media=media, name="연속", canvas=SHORTS)
    builder.add_clip(media, 10.0, 2.0)
    builder.add_clip(media, 30.0, 3.0)
    segments = builder.timeline_json()["tracks"][0]["segments"]
    assert segments[0]["target_timerange"] == {"start": 0, "duration": 2 * US}
    assert segments[1]["target_timerange"] == {"start": 2 * US, "duration": 3 * US}


def test_a_clip_past_the_source_end_is_clamped(tmp_path):
    media = _media(tmp_path, duration=10.0)
    builder = DraftBuilder(media=media, name="초과", canvas=SHORTS)
    builder.add_clip(media, 8.0, 5.0)
    segment = builder.timeline_json()["tracks"][0]["segments"][0]
    assert segment["source_timerange"]["duration"] == 2 * US


# ---------------------------------------------------------------------- 자막


def test_subtitles_create_a_text_track(tmp_path):
    builder = DraftBuilder(media=_media(tmp_path), name="자막", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 10.0)])
    builder.add_subtitles([SubtitleCue(0.0, 2.0, "안녕하세요"), SubtitleCue(2.0, 4.0, "반갑습니다")])
    tracks = {t["type"]: t for t in builder.timeline_json()["tracks"]}
    assert "text" in tracks
    assert len(tracks["text"]["segments"]) == 2


def test_subtitle_sits_above_the_bottom_safe_zone(tmp_path):
    builder = DraftBuilder(media=_media(tmp_path), name="자막", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 10.0)])
    builder.add_subtitles([SubtitleCue(0.0, 2.0, "안녕하세요")])
    segment = [t for t in builder.timeline_json()["tracks"] if t["type"] == "text"][0]["segments"][0]
    # 지뢰 7번: 세로 위치는 transform.y. 음수가 아래쪽.
    assert segment["clip"]["transform"]["y"] == pytest.approx(-0.72)


def test_subtitle_vertical_position_is_configurable(tmp_path):
    builder = DraftBuilder(media=_media(tmp_path), name="자막", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 10.0)])
    builder.add_subtitles(
        [SubtitleCue(0.0, 2.0, "위쪽")], SubtitleStyle(transform_y=0.5)
    )
    segment = [t for t in builder.timeline_json()["tracks"] if t["type"] == "text"][0]["segments"][0]
    assert segment["clip"]["transform"]["y"] == pytest.approx(0.5)


def test_subtitle_text_survives_into_the_material_content(tmp_path):
    builder = DraftBuilder(media=_media(tmp_path), name="자막", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 10.0)])
    builder.add_subtitles([SubtitleCue(0.0, 2.0, "이걸\n할 수 있습니다")])
    material = builder.timeline_json()["materials"]["texts"][0]
    content = json.loads(material["content"])
    assert content["text"] == "이걸\n할 수 있습니다"
    assert content["styles"][0]["range"] == [0, len("이걸\n할 수 있습니다")]


def test_subtitles_render_above_the_video(tmp_path):
    builder = DraftBuilder(media=_media(tmp_path), name="자막", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 10.0)])
    builder.add_subtitles([SubtitleCue(0.0, 2.0, "위에")])
    timeline = builder.timeline_json()
    video_index = timeline["tracks"][0]["segments"][0]["render_index"]
    text_segment = [t for t in timeline["tracks"] if t["type"] == "text"][0]["segments"][0]
    assert text_segment["render_index"] > video_index


def test_blank_and_zero_length_cues_are_dropped(tmp_path):
    builder = DraftBuilder(media=_media(tmp_path), name="자막", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 10.0)])
    builder.add_subtitles([
        SubtitleCue(0.0, 2.0, "   "),
        SubtitleCue(3.0, 3.0, "길이없음"),
        SubtitleCue(4.0, 5.0, "살아남음"),
    ])
    texts = builder.timeline_json()["materials"]["texts"]
    assert len(texts) == 1
    assert json.loads(texts[0]["content"])["text"] == "살아남음"


def test_no_text_track_when_there_are_no_subtitles(tmp_path):
    builder = DraftBuilder(media=_media(tmp_path), name="자막없음", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 10.0)])
    assert [t["type"] for t in builder.timeline_json()["tracks"]] == ["video"]


# -------------------------------------------------------------------- 오디오


def test_audio_creates_an_audio_track(tmp_path):
    narration = tmp_path / "narration.mp3"
    narration.write_bytes(b"fake")
    builder = DraftBuilder(name="보이스", canvas=SHORTS)
    builder.add_clip(_media(tmp_path), 0.0, 8.0)
    builder.add_audio(narration, duration=7.5)
    tracks = {t["type"]: t for t in builder.timeline_json()["tracks"]}
    assert "audio" in tracks
    assert len(tracks["audio"]["segments"]) == 1


def test_audio_segment_lands_at_the_requested_start(tmp_path):
    narration = tmp_path / "line2.mp3"
    narration.write_bytes(b"fake")
    builder = DraftBuilder(name="보이스", canvas=SHORTS)
    builder.add_clip(_media(tmp_path), 0.0, 20.0)
    builder.add_audio(narration, duration=3.0, target_start=5.0, volume=0.8)
    segment = [t for t in builder.timeline_json()["tracks"] if t["type"] == "audio"][0]["segments"][0]
    assert segment["target_timerange"] == {"start": 5 * US, "duration": 3 * US}
    assert segment["volume"] == pytest.approx(0.8)


def test_audio_segments_skip_the_canvas_material(tmp_path):
    """오디오에는 canvas_color 가 필요 없다. 비디오와 참조 개수가 다르다."""
    narration = tmp_path / "n.mp3"
    narration.write_bytes(b"fake")
    builder = DraftBuilder(name="보이스", canvas=SHORTS)
    builder.add_clip(_media(tmp_path), 0.0, 8.0)
    builder.add_audio(narration, duration=7.5)
    timeline = builder.timeline_json()
    audio_segment = [t for t in timeline["tracks"] if t["type"] == "audio"][0]["segments"][0]
    video_segment = timeline["tracks"][0]["segments"][0]
    assert len(video_segment["extra_material_refs"]) == 4
    assert len(audio_segment["extra_material_refs"]) == 3


def test_timeline_length_covers_audio_that_outlasts_the_video(tmp_path):
    narration = tmp_path / "long.mp3"
    narration.write_bytes(b"fake")
    builder = DraftBuilder(name="보이스", canvas=SHORTS)
    builder.add_clip(_media(tmp_path), 0.0, 5.0)
    builder.add_audio(narration, duration=12.0)
    assert builder.timeline_us == 12 * US


def test_zero_length_audio_is_ignored(tmp_path):
    narration = tmp_path / "empty.mp3"
    narration.write_bytes(b"")
    builder = DraftBuilder(media=_media(tmp_path), name="보이스", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 5.0)])
    builder.add_audio(narration, duration=0.0)
    assert [t["type"] for t in builder.timeline_json()["tracks"]] == ["video"]


# ------------------------------------------------------------------- 메타/기타


def test_every_material_appears_in_the_project_meta(tmp_path):
    narration = tmp_path / "n.mp3"
    narration.write_bytes(b"fake")
    media = _media(tmp_path)
    builder = DraftBuilder(media=media, name="메타", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 5.0)])
    builder.add_audio(narration, duration=5.0)
    root = tmp_path / "drafts"
    root.mkdir()
    folder = builder.write(root)
    meta = json.loads((folder / "draft_meta_info.json").read_text("utf-8"))
    names = {Path(e["file_Path"]).name for e in meta["draft_materials"][0]["value"]}
    assert names == {"talk.mp4", "n.mp3"}


def test_meta_duration_matches_the_timeline_with_every_track(tmp_path):
    narration = tmp_path / "n.mp3"
    narration.write_bytes(b"fake")
    builder = DraftBuilder(name="메타", canvas=SHORTS)
    builder.add_clip(_media(tmp_path), 0.0, 4.0)
    builder.add_audio(narration, duration=9.0)
    builder.add_subtitles([SubtitleCue(0.0, 9.0, "자막")])
    root = tmp_path / "drafts"
    root.mkdir()
    folder = builder.write(root)
    meta = json.loads((folder / "draft_meta_info.json").read_text("utf-8"))
    assert meta["tm_duration"] == _timeline(folder)["duration"] == 9 * US


def test_hex_to_rgb_converts_to_unit_floats():
    assert hex_to_rgb("#FFFFFF") == [1.0, 1.0, 1.0]
    assert hex_to_rgb("000000") == [0.0, 0.0, 0.0]
    assert hex_to_rgb("#FF0000") == [1.0, 0.0, 0.0]


def test_hex_to_rgb_rejects_bad_input():
    with pytest.raises(ValueError):
        hex_to_rgb("#FFF")
