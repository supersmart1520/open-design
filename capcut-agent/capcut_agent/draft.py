"""캡컷 드래프트 빌더.

캡컷은 프로젝트를 폴더 하나로 저장한다.

    <드래프트 루트>/<프로젝트 이름>/
        draft_content.json    ← 타임라인 (Windows / 구버전이 읽는 이름)
        draft_info.json       ← 타임라인 (macOS 최신판이 읽는 이름, 내용 동일)
        draft_meta_info.json  ← 프로젝트 메타 (여기 tm_duration 이 빠지면 길이 0으로 뜬다)
        draft_virtual_store.json

지뢰 목록
  1. 타임라인 파일 이름이 버전마다 다르다 → 두 이름으로 같은 내용을 쓴다.
  2. draft_meta_info.json 의 tm_duration(마이크로초)이 없거나 0이면
     캡컷 홈 화면에 00:00 으로 뜨고 열어도 빈 프로젝트가 된다.
  3. 세그먼트의 extra_material_refs 에 speed / canvas / sound_channel_mapping /
     vocal_separation 4종이 없으면 캡컷이 클립을 검은 화면으로 그리거나 죽는다.
  4. 경로는 반드시 절대경로. Windows 는 역슬래시로 저장된다.
  5. 캡컷이 실행 중이면 새 드래프트가 목록에 안 나타난다 → 재시작 안내.
"""

from __future__ import annotations

import json
import os
import platform
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .media import MediaInfo
from .paths import TIMELINE_FILENAMES
from .silence import Range

#: 캡컷 타임라인은 전부 마이크로초 정수다.
US = 1_000_000


def new_id() -> str:
    return str(uuid.uuid4()).upper()


def _now_us() -> int:
    return int(time.time() * US)


def _native_path(path: Path) -> str:
    """캡컷이 저장하는 형식 그대로. Windows 는 역슬래시."""
    text = str(path)
    if os.name == "nt":
        return text.replace("/", "\\")
    return text


def _platform_block() -> dict:
    system = platform.system()
    return {
        "app_id": 359289,
        "app_source": "cc",
        "app_version": "5.9.0",
        "device_id": "",
        "hard_disk_id": "",
        "mac_address": "",
        "os": {"Darwin": "mac", "Windows": "windows"}.get(system, "mac"),
        "os_version": platform.release(),
    }


@dataclass
class DraftBuilder:
    media: MediaInfo
    name: str
    draft_id: str = field(default_factory=new_id)

    _video_material_id: str = field(default="", init=False)
    _segments: list[dict] = field(default_factory=list, init=False)
    _text_segments: list[dict] = field(default_factory=list, init=False)
    _materials: dict[str, list[dict]] = field(default_factory=dict, init=False)
    _timeline_us: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._materials = {
            "videos": [], "audios": [], "texts": [], "canvases": [], "speeds": [],
            "sound_channel_mappings": [], "vocal_separations": [], "material_animations": [],
        }
        self._video_material_id = new_id()
        self._materials["videos"].append(self._video_material())

    # ---------------------------------------------------------------- materials

    def _video_material(self) -> dict:
        return {
            "audio_fade": None, "cartoon_path": "", "category_id": "",
            "category_name": "local", "check_flag": 62978047,
            "crop": {
                "lower_left_x": 0.0, "lower_left_y": 1.0,
                "lower_right_x": 1.0, "lower_right_y": 1.0,
                "upper_left_x": 0.0, "upper_left_y": 0.0,
                "upper_right_x": 1.0, "upper_right_y": 0.0,
            },
            "crop_ratio": "free", "crop_scale": 1.0,
            "duration": self.media.duration_us,
            "extra_type_option": 0, "formula_id": "", "freeze": None,
            "has_audio": self.media.has_audio,
            "height": self.media.height, "id": self._video_material_id,
            "intensifies_audio_path": "", "intensifies_path": "",
            "is_ai_generate_content": False, "is_copyright": False,
            "is_text_edit_overdub": False, "is_unified_beauty_mode": False,
            "local_id": "", "local_material_id": "", "material_id": "",
            "material_name": self.media.path.name, "material_url": "",
            "matting": {
                "flag": 0, "has_use_quick_brush": False,
                "has_use_quick_eraser": False, "interactiveTime": [],
                "path": "", "strokes": [],
            },
            "media_path": "", "object_locked": None, "origin_material_id": "",
            "path": _native_path(self.media.path), "picture_from": "none",
            "picture_set_category_id": "", "picture_set_category_name": "",
            "request_id": "", "reverse_intensifies_path": "", "reverse_path": "",
            "smart_motion": None, "source": 0, "source_platform": 0, "stable": None,
            "team_id": "", "type": "video",
            "video_algorithm": {
                "algorithms": [], "complement_frame_config": None, "deflicker": None,
                "gameplay_configs": [], "motion_blur_config": None,
                "noise_reduction": None, "path": "", "quality_enhance": None,
                "time_range": None,
            },
            "width": self.media.width,
        }

    def _segment_extras(self) -> list[str]:
        """세그먼트마다 필요한 부속 머티리얼 4종. 빠지면 캡컷이 렌더를 못 한다."""
        speed_id, canvas_id, mapping_id, vocal_id = (new_id() for _ in range(4))
        self._materials["speeds"].append(
            {"curve_speed": None, "id": speed_id, "mode": 0, "speed": 1.0, "type": "speed"}
        )
        self._materials["canvases"].append(
            {"album_image": "", "blur": 0.0, "color": "", "id": canvas_id,
             "image": "", "image_id": "", "image_name": "", "source_platform": 0,
             "team_id": "", "type": "canvas_color"}
        )
        self._materials["sound_channel_mappings"].append(
            {"audio_channel_mapping": 0, "id": mapping_id,
             "is_config_open": False, "type": "none"}
        )
        self._materials["vocal_separations"].append(
            {"choice": 0, "id": vocal_id, "production_path": "",
             "removed_sounds": [], "time_range": None, "type": "vocal_separation"}
        )
        return [speed_id, canvas_id, mapping_id, vocal_id]

    # ---------------------------------------------------------------- timeline

    def add_video_cuts(self, keeps: list[Range]) -> None:
        """보존 구간들을 잘라 붙여 점프컷 타임라인을 만든다."""
        cursor_us = 0
        for keep in keeps:
            start_us = int(round(keep.start * US))
            length_us = int(round(keep.length * US))
            if length_us <= 0:
                continue
            # 소스 끝을 넘어가는 요청은 캡컷에서 클립이 사라지는 원인이 된다.
            length_us = min(length_us, self.media.duration_us - start_us)
            if length_us <= 0:
                continue
            self._segments.append(
                self._video_segment(start_us, length_us, cursor_us, len(self._segments))
            )
            cursor_us += length_us
        self._timeline_us = cursor_us

    def _video_segment(
        self, source_start_us: int, length_us: int, target_start_us: int, index: int
    ) -> dict:
        return {
            "cartoon": False,
            "clip": {
                "alpha": 1.0,
                "flip": {"horizontal": False, "vertical": False},
                "rotation": 0.0, "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": 0.0},
            },
            "common_keyframes": [], "enable_adjust": True,
            "enable_color_correct_adjust": False, "enable_color_curves": True,
            "enable_color_match_adjust": False, "enable_color_wheels": True,
            "enable_lut": True, "enable_smart_color_adjust": False,
            "extra_material_refs": self._segment_extras(),
            "group_id": "",
            "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
            "id": new_id(), "intensifies_audio": False, "is_placeholder": False,
            "is_tone_modify": False, "keyframe_refs": [], "last_nonzero_volume": 1.0,
            "material_id": self._video_material_id, "render_index": 0,
            "responsive_layout": {
                "enable": False, "horizontal_pos_layout": 0, "size_layout": 0,
                "target_follow": "", "vertical_pos_layout": 0,
            },
            "reverse": False,
            "source_timerange": {"duration": length_us, "start": source_start_us},
            "speed": 1.0,
            "target_timerange": {"duration": length_us, "start": target_start_us},
            "template_id": "", "template_scene": "default", "track_attribute": 0,
            "track_render_index": index,
            "uniform_scale": {"on": True, "value": 1.0},
            "visible": True, "volume": 1.0,
        }

    # ---------------------------------------------------------------- writing

    def _tracks(self) -> list[dict]:
        tracks = [{
            "attribute": 0, "flag": 0, "id": new_id(), "is_default_name": True,
            "name": "", "segments": self._segments, "type": "video",
        }]
        if self._text_segments:
            tracks.append({
                "attribute": 0, "flag": 0, "id": new_id(), "is_default_name": True,
                "name": "", "segments": self._text_segments, "type": "text",
            })
        return tracks

    def timeline_json(self) -> dict:
        return {
            "canvas_config": {
                "height": self.media.height, "ratio": "original",
                "width": self.media.width,
            },
            "color_space": 0,
            "config": {
                "adjust_max_index": 1, "attachment_info": [], "combination_max_index": 1,
                "export_range": None, "extract_audio_last_index": 1,
                "lyrics_recognition_id": "", "lyrics_sync": True,
                "lyrics_taskinfo": [], "maintrack_adsorb": True,
                "material_save_mode": 0, "multi_language_current": "none",
                "multi_language_list": [], "multi_language_main": "none",
                "multi_language_mode": "none", "original_sound_last_index": 1,
                "record_audio_last_index": 1, "sticker_max_index": 1,
                "subtitle_recognition_id": "", "subtitle_sync": True,
                "subtitle_taskinfo": [], "system_font_list": [],
                "video_mute": False, "zoom_info_params": None,
            },
            "cover": None, "create_time": 0, "duration": self._timeline_us,
            "extra_info": None, "fps": self.media.fps,
            "free_render_index_mode_on": False, "group_container": None,
            "id": self.draft_id, "keyframe_graph_list": [],
            "keyframes": {
                "adjusts": [], "audios": [], "effects": [], "filters": [],
                "handwrites": [], "stickers": [], "texts": [], "videos": [],
            },
            "last_modified_platform": _platform_block(),
            "materials": {
                "ai_translates": [], "audio_balances": [], "audio_effects": [],
                "audio_fades": [], "audio_track_indexes": [],
                "audios": self._materials["audios"],
                "beats": [], "canvases": self._materials["canvases"],
                "chromas": [], "color_curves": [], "digital_humans": [],
                "drafts": [], "effects": [], "flowers": [], "green_screens": [],
                "handwrites": [], "hsl": [], "images": [], "log_color_wheels": [],
                "loudnesses": [], "manual_deformations": [],
                "material_animations": self._materials["material_animations"],
                "material_colors": [], "multi_language_refs": [], "placeholders": [],
                "plugin_effects": [], "primary_color_wheels": [], "realtime_denoises": [],
                "shapes": [], "smart_crops": [], "smart_relights": [],
                "sound_channel_mappings": self._materials["sound_channel_mappings"],
                "speeds": self._materials["speeds"], "stickers": [], "tail_leaders": [],
                "text_templates": [], "texts": self._materials["texts"],
                "time_marks": [], "transitions": [], "video_effects": [],
                "video_trackings": [], "videos": self._materials["videos"],
                "vocal_beautifys": [],
                "vocal_separations": self._materials["vocal_separations"],
            },
            "mutable_config": None, "name": "", "new_version": "110.0.0",
            "platform": _platform_block(), "relations": [],
            "render_index_track_mode_on": True, "retouch_cover": None,
            "source": "default", "static_cover_image_path": "", "time_marks": None,
            "tracks": self._tracks(), "update_time": 0, "version": 360000,
        }

    def meta_json(self, folder: Path, root: Path) -> dict:
        now_us = _now_us()
        return {
            "cloud_package_completed_time": "",
            "draft_cloud_capcut_purchase_info": "",
            "draft_cloud_last_action_download": False,
            "draft_cloud_materials": [], "draft_cloud_purchase_info": "",
            "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "",
            "draft_cover": "draft_cover.jpg", "draft_deeplink_url": "",
            "draft_enterprise_info": {
                "draft_enterprise_extra": "", "draft_enterprise_id": "",
                "draft_enterprise_name": "", "enterprise_material": [],
            },
            "draft_fold_path": _native_path(folder), "draft_id": self.draft_id,
            "draft_is_ai_packaging_used": False, "draft_is_ai_shorts": False,
            "draft_is_ai_translate": False, "draft_is_article_video_draft": False,
            "draft_is_from_deeplink": "false", "draft_is_invisible": False,
            "draft_materials": [
                {"type": 0, "value": [self._material_meta(now_us)]},
                {"type": 1, "value": []}, {"type": 2, "value": []},
                {"type": 3, "value": []}, {"type": 6, "value": []},
                {"type": 7, "value": []}, {"type": 8, "value": []},
            ],
            "draft_materials_copied_info": [], "draft_name": self.name,
            "draft_new_version": "", "draft_removable_storage_device": "",
            "draft_root_path": _native_path(root), "draft_segment_extra_info": [],
            "draft_timeline_materials_size_": 0, "draft_type": "",
            "tm_draft_cloud_completed": "", "tm_draft_cloud_modified": 0,
            "tm_draft_create": now_us, "tm_draft_modified": now_us,
            "tm_draft_removed": 0,
            # 지뢰 2번: 이 값이 캡컷 홈 화면의 프로젝트 길이다.
            "tm_duration": self._timeline_us,
        }

    def _material_meta(self, now_us: int) -> dict:
        return {
            "create_time": int(now_us / US), "duration": self.media.duration_us,
            "extra_info": self.media.path.name,
            "file_Path": _native_path(self.media.path),
            "height": self.media.height, "id": new_id(),
            "import_time": int(now_us / US), "import_time_ms": now_us,
            "item_source": 1, "md5": "", "metetype": "video",
            "roughcut_time_range": {"duration": self.media.duration_us, "start": 0},
            "sub_time_range": {"duration": -1, "start": -1},
            "type": 0, "width": self.media.width,
        }

    def write(self, root: Path) -> Path:
        folder = root / self.name
        folder.mkdir(parents=True, exist_ok=True)

        timeline = self.timeline_json()
        # 지뢰 1번: 버전마다 읽는 파일 이름이 달라 두 이름으로 같은 내용을 쓴다.
        for filename in TIMELINE_FILENAMES:
            _write_json(folder / filename, timeline)
        _write_json(folder / "draft_meta_info.json", self.meta_json(folder, root))
        _write_json(
            folder / "draft_virtual_store.json",
            {"draft_materials": [],
             "draft_virtual_store": [
                 {"type": 0, "value": []}, {"type": 1, "value": []},
                 {"type": 2, "value": []},
             ]},
        )
        return folder


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
