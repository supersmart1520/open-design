"""회전 메타데이터와 미디어 반입 — 실사용에서 터진 두 가지.

1. 아이폰 세로 .MOV 는 1920x1080 + 회전 90° 로 저장된다. 회전을 무시하면
   세로 영상을 가로 캔버스에 넣어 비율이 통째로 뒤집힌다.
2. macOS 캡컷은 샌드박스라 컨테이너 밖 파일을 못 읽는다
   ("파일에 액세스할 수 없음"). 미디어를 드래프트 폴더로 들여야 한다.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from capcut_agent.draft import (
    MATERIALS_DIRNAME,
    DraftBuilder,
    default_media_mode,
)
from capcut_agent.framing import SHORTS
from capcut_agent.media import MediaInfo, media_info_from_probe, read_rotation
from capcut_agent.silence import Range

BASE_PROBE = {
    "format": {"duration": "10"},
    "streams": [
        {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30/1"},
        {"codec_type": "audio"},
    ],
}


def _probe_with(**video_keys) -> dict:
    data = copy.deepcopy(BASE_PROBE)
    data["streams"][0].update(video_keys)
    return data


# ------------------------------------------------------------------- 회전


def test_no_rotation_keeps_the_dimensions():
    info = media_info_from_probe(BASE_PROBE, Path("x.mp4"))
    assert (info.width, info.height, info.rotation) == (1920, 1080, 0)


def test_legacy_rotate_tag_swaps_the_dimensions():
    info = media_info_from_probe(_probe_with(tags={"rotate": "90"}), Path("x.mov"))
    assert (info.width, info.height, info.rotation) == (1080, 1920, 90)


def test_display_matrix_rotation_swaps_the_dimensions():
    """요즘 ffprobe 가 쓰는 방식. 반시계 음수라 부호를 뒤집어야 한다."""
    info = media_info_from_probe(
        _probe_with(side_data_list=[{"rotation": -90}]), Path("x.mov")
    )
    assert (info.width, info.height, info.rotation) == (1080, 1920, 90)


def test_two_seventy_also_swaps():
    info = media_info_from_probe(_probe_with(tags={"rotate": "270"}), Path("x.mov"))
    assert (info.width, info.height) == (1080, 1920)


def test_one_eighty_does_not_swap():
    info = media_info_from_probe(_probe_with(tags={"rotate": "180"}), Path("x.mov"))
    assert (info.width, info.height, info.rotation) == (1920, 1080, 180)


def test_a_rotated_portrait_clip_needs_no_crop_in_a_shorts_canvas(tmp_path):
    """회전을 반영하면 아이폰 세로 영상은 9:16 에 그대로 들어간다."""
    source = tmp_path / "IMG_0537.MOV"
    source.write_bytes(b"fake")
    info = media_info_from_probe(_probe_with(tags={"rotate": "90"}), source)
    builder = DraftBuilder(media=info, name="세로", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 5.0)])
    crop = builder.timeline_json()["materials"]["videos"][0]["crop"]
    assert crop["upper_left_x"] == 0.0 and crop["upper_right_x"] == 1.0


def test_garbage_rotation_values_are_ignored():
    assert read_rotation({"tags": {"rotate": "나인티"}}) == 0
    assert read_rotation({"side_data_list": [{"rotation": None}]}) == 0
    assert read_rotation({}) == 0


# -------------------------------------------------------------- 미디어 반입


@pytest.fixture
def staged(tmp_path: Path):
    source = tmp_path / "원본" / "IMG_0537.MOV"
    source.parent.mkdir()
    source.write_bytes(b"pretend-video-bytes")
    media = MediaInfo(path=source, width=1080, height=1920, fps=30.0,
                      duration=10.0, has_audio=True)
    builder = DraftBuilder(media=media, name="반입", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 5.0)])
    root = tmp_path / "drafts"
    root.mkdir()
    return builder, root, source


def test_media_lands_inside_the_draft_folder(staged):
    builder, root, source = staged
    folder = builder.write(root, media_mode="auto")
    copied = folder / MATERIALS_DIRNAME / source.name
    assert copied.is_file()
    assert copied.read_bytes() == source.read_bytes()


def test_the_draft_points_at_the_staged_copy_not_the_original(staged):
    builder, root, source = staged
    folder = builder.write(root, media_mode="auto")
    timeline = json.loads((folder / "draft_content.json").read_text("utf-8"))
    path = Path(timeline["materials"]["videos"][0]["path"])
    assert path == folder / MATERIALS_DIRNAME / source.name
    assert path.is_file()


def test_the_project_meta_points_at_the_staged_copy_too(staged):
    builder, root, source = staged
    folder = builder.write(root, media_mode="auto")
    meta = json.loads((folder / "draft_meta_info.json").read_text("utf-8"))
    staged_path = Path(meta["draft_materials"][0]["value"][0]["file_Path"])
    assert staged_path == folder / MATERIALS_DIRNAME / source.name


def test_auto_mode_hardlinks_when_it_can(staged):
    """같은 볼륨이면 하드링크라 용량이 늘지 않는다."""
    builder, root, source = staged
    folder = builder.write(root, media_mode="auto")
    copied = folder / MATERIALS_DIRNAME / source.name
    assert os.stat(copied).st_ino == os.stat(source).st_ino


def test_copy_mode_makes_a_real_copy(staged):
    builder, root, source = staged
    folder = builder.write(root, media_mode="copy")
    copied = folder / MATERIALS_DIRNAME / source.name
    assert os.stat(copied).st_ino != os.stat(source).st_ino
    assert copied.read_bytes() == source.read_bytes()


def test_none_mode_leaves_the_original_path(staged):
    builder, root, source = staged
    folder = builder.write(root, media_mode="none")
    timeline = json.loads((folder / "draft_content.json").read_text("utf-8"))
    assert Path(timeline["materials"]["videos"][0]["path"]) == source
    assert not (folder / MATERIALS_DIRNAME).exists()


def test_audio_is_staged_as_well(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    narration = tmp_path / "n.mp3"
    narration.write_bytes(b"n")
    media = MediaInfo(path=video, width=1080, height=1920, fps=30.0,
                      duration=10.0, has_audio=True)
    builder = DraftBuilder(media=media, name="오디오반입", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 5.0)])
    builder.add_audio(narration, duration=5.0)
    root = tmp_path / "drafts"
    root.mkdir()
    folder = builder.write(root, media_mode="copy")
    assert (folder / MATERIALS_DIRNAME / "n.mp3").is_file()


def test_two_sources_with_the_same_name_do_not_collide(tmp_path):
    first = tmp_path / "a" / "clip.mp4"
    second = tmp_path / "b" / "clip.mp4"
    for path, payload in ((first, b"first"), (second, b"second")):
        path.parent.mkdir()
        path.write_bytes(payload)
    builder = DraftBuilder(name="충돌", canvas=SHORTS)
    for path in (first, second):
        builder.add_clip(
            MediaInfo(path=path, width=1080, height=1920, fps=30.0,
                      duration=10.0, has_audio=True),
            0.0, 3.0,
        )
    root = tmp_path / "drafts"
    root.mkdir()
    folder = builder.write(root, media_mode="copy")
    staged_files = sorted(p.name for p in (folder / MATERIALS_DIRNAME).iterdir())
    assert len(staged_files) == 2
    payloads = {(folder / MATERIALS_DIRNAME / n).read_bytes() for n in staged_files}
    assert payloads == {b"first", b"second"}


def test_a_missing_source_is_left_alone(tmp_path):
    ghost = tmp_path / "없는파일.mp4"
    media = MediaInfo(path=ghost, width=1080, height=1920, fps=30.0,
                      duration=10.0, has_audio=True)
    builder = DraftBuilder(media=media, name="없음", canvas=SHORTS)
    builder.add_video_cuts([Range(0.0, 5.0)])
    root = tmp_path / "drafts"
    root.mkdir()
    folder = builder.write(root, media_mode="copy")
    timeline = json.loads((folder / "draft_content.json").read_text("utf-8"))
    assert Path(timeline["materials"]["videos"][0]["path"]) == ghost


def test_macos_defaults_to_staging_and_others_do_not():
    assert default_media_mode("Darwin") == "auto"
    assert default_media_mode("Windows") == "none"
    assert default_media_mode("Linux") == "none"
