"""드래프트 빌더 구조 검증.

캡컷을 열어야만 알 수 있는 것도 많지만, 지뢰 목록만큼은 코드로 못박아 둔다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capcut_agent.draft import US, DraftBuilder
from capcut_agent.media import MediaInfo
from capcut_agent.paths import TIMELINE_FILENAMES
from capcut_agent.silence import Range


@pytest.fixture
def media(tmp_path: Path) -> MediaInfo:
    source = tmp_path / "talk.mp4"
    source.write_bytes(b"not-a-real-mp4")
    return MediaInfo(
        path=source, width=1920, height=1080, fps=30.0, duration=10.0, has_audio=True
    )


@pytest.fixture
def built(media: MediaInfo, tmp_path: Path):
    builder = DraftBuilder(media=media, name="테스트-드래프트")
    builder.add_video_cuts([Range(0.0, 1.5), Range(2.4, 5.0), Range(6.2, 10.0)])
    root = tmp_path / "drafts"
    root.mkdir()
    return builder, builder.write(root), root


def _timeline(folder: Path) -> dict:
    return json.loads((folder / "draft_content.json").read_text("utf-8"))


def test_writes_both_timeline_filenames_with_identical_content(built):
    _, folder, _ = built
    payloads = [json.loads((folder / n).read_text("utf-8")) for n in TIMELINE_FILENAMES]
    assert len(payloads) == 2
    assert payloads[0] == payloads[1]


def test_timeline_duration_is_sum_of_kept_ranges(built):
    _, folder, _ = built
    expected = int(round((1.5 + 2.6 + 3.8) * US))
    assert _timeline(folder)["duration"] == pytest.approx(expected, abs=2)


def test_meta_tm_duration_matches_timeline(built):
    _, folder, _ = built
    meta = json.loads((folder / "draft_meta_info.json").read_text("utf-8"))
    # 지뢰 2번: 여기가 0이면 캡컷 홈에서 00:00 으로 뜬다.
    assert meta["tm_duration"] == _timeline(folder)["duration"] > 0


def test_segments_are_contiguous_on_the_timeline(built):
    _, folder, _ = built
    segments = _timeline(folder)["tracks"][0]["segments"]
    assert len(segments) == 3
    cursor = 0
    for segment in segments:
        assert segment["target_timerange"]["start"] == cursor
        cursor += segment["target_timerange"]["duration"]


def test_segments_point_at_the_right_source_times(built):
    _, folder, _ = built
    starts = [s["source_timerange"]["start"] for s in _timeline(folder)["tracks"][0]["segments"]]
    assert starts == [0, int(2.4 * US), int(6.2 * US)]


def test_every_segment_carries_four_extra_material_refs(built):
    _, folder, _ = built
    materials = _timeline(folder)["materials"]
    known = {
        m["id"]
        for key in ("speeds", "canvases", "sound_channel_mappings", "vocal_separations")
        for m in materials[key]
    }
    for segment in _timeline(folder)["tracks"][0]["segments"]:
        refs = segment["extra_material_refs"]
        # 지뢰 3번: 4종이 다 있어야 캡컷이 클립을 그린다.
        assert len(refs) == 4
        assert set(refs) <= known


def test_all_segments_share_one_video_material(built):
    _, folder, _ = built
    timeline = _timeline(folder)
    videos = timeline["materials"]["videos"]
    assert len(videos) == 1
    assert {s["material_id"] for s in timeline["tracks"][0]["segments"]} == {videos[0]["id"]}


def test_paths_are_absolute(built):
    _, folder, root = built
    timeline = _timeline(folder)
    meta = json.loads((folder / "draft_meta_info.json").read_text("utf-8"))
    assert Path(timeline["materials"]["videos"][0]["path"]).is_absolute()
    assert Path(meta["draft_fold_path"]) == folder
    assert Path(meta["draft_root_path"]) == root


def test_canvas_matches_source_resolution(built):
    _, folder, _ = built
    canvas = _timeline(folder)["canvas_config"]
    assert (canvas["width"], canvas["height"]) == (1920, 1080)


def test_cut_never_reaches_past_the_source(media):
    builder = DraftBuilder(media=media, name="overrun")
    # 소스는 10초인데 12초까지 요청한 경우.
    builder.add_video_cuts([Range(8.0, 12.0)])
    segment = builder.timeline_json()["tracks"][0]["segments"][0]
    end = segment["source_timerange"]["start"] + segment["source_timerange"]["duration"]
    assert end <= media.duration_us


def test_draft_folder_is_named_after_the_project(built):
    builder, folder, _ = built
    assert folder.name == builder.name == "테스트-드래프트"
