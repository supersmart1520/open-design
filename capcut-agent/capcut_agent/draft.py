"""캡컷 드래프트 빌더.

캡컷은 프로젝트를 폴더 하나로 저장한다.

    <드래프트 루트>/<프로젝트 이름>/
        draft_content.json    ← 타임라인 (Windows / 구버전이 읽는 이름)
        draft_info.json       ← 타임라인 (macOS 최신판이 읽는 이름, 내용 동일)
        draft_meta_info.json  ← 프로젝트 메타 (여기 tm_duration 이 빠지면 길이 0으로 뜬다)
        draft_virtual_store.json

트랙 셋을 만든다.
    video  소스 클립 (컷 적용, 필요하면 9:16 크롭)
    audio  나레이션 / BGM (모드 B)
    text   자막

지뢰 목록
  1. 타임라인 파일 이름이 버전마다 다르다 → 두 이름으로 같은 내용을 쓴다.
  2. draft_meta_info.json 의 tm_duration(마이크로초)이 없거나 0이면
     캡컷 홈 화면에 00:00 으로 뜨고 열어도 빈 프로젝트가 된다.
  3. 세그먼트의 extra_material_refs 에 speed / canvas / sound_channel_mapping /
     vocal_separation 4종이 없으면 캡컷이 클립을 검은 화면으로 그리거나 죽는다.
  4. 경로는 반드시 절대경로. Windows 는 역슬래시로 저장된다.
  5. 캡컷이 실행 중이면 새 드래프트가 목록에 안 나타난다 → 재시작 안내.
  6. 원본 이동은 드래프트 생성보다 먼저. 경로가 박혀 들어가므로.
  7. 자막 세로 위치는 segment.clip.transform.y. 화면 중앙이 0, 아래가 음수.
  8. **macOS 캡컷은 샌드박스 안에서 돈다.** 컨테이너 밖 경로(~/CapCutAgent/inbox 등)는
     읽지 못하고 "파일에 액세스할 수 없음 / Unsupported Media" 로 뜬다. UI 로 가져오면
     macOS 가 파일 선택 창을 통해 권한을 주지만, 우리가 손으로 쓴 드래프트에는 그게 없다.
     그래서 미디어를 **드래프트 폴더 안**(= 컨테이너 안)으로 옮겨 놓고 그 경로를 가리킨다.
     같은 볼륨이면 하드링크라 용량을 더 쓰지 않는다.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .framing import Anchor, Canvas, crop_ratio_label, crop_window
from .media import MediaInfo
from .paths import TIMELINE_FILENAMES
from .silence import Range
from .subtitle import SubtitleCue

#: 캡컷 타임라인은 전부 마이크로초 정수다.
US = 1_000_000

#: 자막은 영상보다 위에 그려져야 한다. 캡컷이 쓰는 대역을 따른다.
_TEXT_RENDER_BASE = 14000
#: 제목은 말하는 자막보다 더 위.
_TITLE_RENDER_BASE = 15000


def new_id() -> str:
    return str(uuid.uuid4()).upper()


def _now_us() -> int:
    return int(time.time() * US)


def _us(seconds: float) -> int:
    return int(round(seconds * US))


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


def hex_to_rgb(value: str) -> list[float]:
    """#RRGGBB → [r, g, b] 0~1 실수. 캡컷 텍스트 스타일이 쓰는 형식."""
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"#RRGGBB 형식이 아닙니다: {value}")
    return [int(text[i : i + 2], 16) / 255 for i in (0, 2, 4)]


#: 말하는 자막의 기본 세로 위치 (캔버스 픽셀, 중앙이 0, 위가 양수).
SPEECH_Y_PX = -850
#: 제목 자막의 기본 세로 위치 (캔버스 픽셀).
TITLE_Y_PX = 1400


def y_px_to_normalized(pixels: float, canvas_height: int) -> float:
    """캡컷 UI 가 보여주는 픽셀 값을 드래프트 JSON 의 정규화 값으로.

    JSON 의 transform.y 는 '캔버스 절반 높이 = 1' 인 단위다. UI 는 중앙 기준
    픽셀을 보여준다. 그래서 절반 높이로 나눈다.
    """
    half = max(canvas_height, 1) / 2
    return pixels / half


#: 자막 등장 모션.
#:
#: 캡컷 내장 애니메이션(material_animations)은 캡컷 자산 카탈로그의 resource_id 를
#: 요구한다. 임의로 지어내면 아무것도 안 뜨거나 프로젝트가 깨진다. 그래서
#: **키프레임**으로 넣는다 — 값이 전부 숫자라 외부 의존이 없고 확실히 렌더된다.
Motion = Literal["none", "fade", "pop", "rise"]

#: 모션이 끝나는 데 걸리는 시간 (초). 자막이 짧으면 그 길이에 맞춰 줄어든다.
MOTION_SECONDS = 0.28


def _keyframe(time_offset_us: int, value: float) -> dict:
    return {
        "curveType": "Line", "graphID": "", "id": new_id(),
        "kf_type": 0, "time_offset": time_offset_us, "values": [value],
    }


def _keyframe_track(property_type: str, points: list[tuple[int, float]]) -> dict:
    return {
        "id": new_id(),
        "keyframe_list": [_keyframe(offset, value) for offset, value in points],
        "material_id": "",
        "property_type": property_type,
    }


def motion_keyframes(motion: Motion, duration: float, settle_y: float) -> list[dict]:
    """등장 모션을 키프레임으로. settle_y 는 모션이 끝난 뒤 자리잡을 세로 위치."""
    if motion == "none" or duration <= 0:
        return []
    span = min(MOTION_SECONDS, duration * 0.6)
    full = _us(span)
    peak = _us(span * 0.65)

    if motion == "fade":
        return [_keyframe_track("KFTypeAlpha", [(0, 0.0), (full, 1.0)])]

    if motion == "pop":
        # 작게 들어와 살짝 넘쳤다가 제자리. 숏폼에서 가장 흔한 등장.
        scale = [(0, 0.72), (peak, 1.06), (full, 1.0)]
        return [
            _keyframe_track("KFTypeScaleX", scale),
            _keyframe_track("KFTypeScaleY", list(scale)),
            _keyframe_track("KFTypeAlpha", [(0, 0.0), (peak, 1.0)]),
        ]

    # rise — 아래에서 올라와 자리잡는다. 위치는 **절대값**이라야 한다.
    # 상대값으로 두면 공들여 맞춘 transform_y 를 키프레임이 덮어쓴다.
    return [
        _keyframe_track("KFTypePositionY", [(0, settle_y - 0.06), (full, settle_y)]),
        _keyframe_track("KFTypeAlpha", [(0, 0.0), (full, 1.0)]),
    ]


@dataclass
class SubtitleStyle:
    """자막 모양. 숏폼 기본값은 1080x1920 에서 읽히도록 맞춰 뒀다."""

    font_size: int = 12
    color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: float = 0.08
    #: 세로 위치(정규화). 캡컷 좌표계는 화면 중앙이 0, 아래가 음수.
    transform_y: float = -0.72
    #: 가로 위치(정규화). 0 = 가운데.
    transform_x: float = 0.0
    #: 1 = 가운데 정렬.
    alignment: int = 1
    line_max_width: float = 0.82
    line_spacing: float = 0.02
    #: 등장 모션.
    motion: Motion = "pop"

    @classmethod
    def from_pixels(
        cls, y_px: float, canvas_height: int, **kwargs
    ) -> SubtitleStyle:
        """캡컷 UI 의 픽셀 값으로 스타일을 만든다."""
        return cls(transform_y=y_px_to_normalized(y_px, canvas_height), **kwargs)


#: 드래프트 폴더 안에서 미디어를 두는 자리.
MATERIALS_DIRNAME = "materials"

#: 미디어 반입 모드. auto = 하드링크 시도 후 실패하면 복사.
MediaMode = Literal["auto", "link", "copy", "none"]


def default_media_mode(system: str | None = None) -> MediaMode:
    """macOS 는 샌드박스 때문에 반입이 필수다. 나머지는 원본을 그대로 가리킨다."""
    return "auto" if (system or platform.system()) == "Darwin" else "none"


@dataclass
class AudioClip:
    """오디오 트랙에 얹을 한 토막."""

    path: Path
    duration: float
    target_start: float = 0.0
    volume: float = 1.0
    source_start: float = 0.0


@dataclass
class DraftBuilder:
    #: 주 소스. cut / highlight 모드는 이걸 쓴다. 모드 B 는 add_clip 으로 넣는다.
    media: MediaInfo | None = None
    name: str = ""
    #: None 이면 주 소스 해상도를 그대로 쓴다 (크롭 없음).
    canvas: Canvas | None = None
    crop_anchor: Anchor = "center"
    fps: float = 30.0
    draft_id: str = field(default_factory=new_id)

    _video_ids: dict[str, str] = field(default_factory=dict, init=False)
    _segments: list[dict] = field(default_factory=list, init=False)
    _audio_segments: list[dict] = field(default_factory=list, init=False)
    _text_segments: list[dict] = field(default_factory=list, init=False)
    _title_segments: list[dict] = field(default_factory=list, init=False)
    _materials: dict[str, list[dict]] = field(default_factory=dict, init=False)
    _meta_entries: list[dict] = field(default_factory=list, init=False)
    _video_cursor_us: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._materials = {
            "videos": [], "audios": [], "texts": [], "canvases": [], "speeds": [],
            "sound_channel_mappings": [], "vocal_separations": [],
            "material_animations": [],
        }
        if self.canvas is None and self.media is not None:
            # 기존 동작: 소스 해상도를 그대로 캔버스로.
            self.canvas = Canvas(self.media.width, self.media.height)
        if self.canvas is None:
            raise ValueError("canvas 나 media 중 하나는 있어야 합니다.")
        if self.media is not None:
            self.fps = self.media.fps
            self._register_video(self.media)

    # ------------------------------------------------------------- properties

    @property
    def timeline_us(self) -> int:
        """전체 길이. 비디오·오디오·자막 중 가장 늦게 끝나는 지점."""
        ends = [self._video_cursor_us]
        for group in (self._audio_segments, self._text_segments, self._title_segments):
            ends += [
                s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                for s in group
            ]
        return max(ends) if ends else 0

    @property
    def is_original_canvas(self) -> bool:
        return (
            self.media is not None
            and self.canvas is not None
            and self.canvas.width == self.media.width
            and self.canvas.height == self.media.height
        )

    # -------------------------------------------------------------- materials

    def _register_video(self, media: MediaInfo) -> str:
        """영상 머티리얼을 등록하고 id 를 돌려준다. 같은 파일은 한 번만."""
        key = str(media.path)
        existing = self._video_ids.get(key)
        if existing:
            return existing
        material_id = new_id()
        self._video_ids[key] = material_id
        self._materials["videos"].append(self._video_material(media, material_id))
        self._meta_entries.append(self._material_meta(media, "video"))
        return material_id

    def _crop_for(self, media: MediaInfo) -> tuple[dict, str]:
        assert self.canvas is not None
        window = crop_window(media.width, media.height, self.canvas, self.crop_anchor)
        label = "free" if window.is_full_frame else crop_ratio_label(self.canvas)
        return window.to_capcut(), label

    def _video_material(self, media: MediaInfo, material_id: str) -> dict:
        crop, crop_label = self._crop_for(media)
        return {
            "audio_fade": None, "cartoon_path": "", "category_id": "",
            "category_name": "local", "check_flag": 62978047,
            "crop": crop, "crop_ratio": crop_label, "crop_scale": 1.0,
            "duration": media.duration_us,
            "extra_type_option": 0, "formula_id": "", "freeze": None,
            "has_audio": media.has_audio,
            "height": media.height, "id": material_id,
            "intensifies_audio_path": "", "intensifies_path": "",
            "is_ai_generate_content": False, "is_copyright": False,
            "is_text_edit_overdub": False, "is_unified_beauty_mode": False,
            "local_id": "", "local_material_id": "", "material_id": "",
            "material_name": media.path.name, "material_url": "",
            "matting": {
                "flag": 0, "has_use_quick_brush": False,
                "has_use_quick_eraser": False, "interactiveTime": [],
                "path": "", "strokes": [],
            },
            "media_path": "", "object_locked": None, "origin_material_id": "",
            "path": _native_path(media.path), "picture_from": "none",
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
            "width": media.width,
        }

    def _audio_material(self, clip: AudioClip, material_id: str) -> dict:
        return {
            "app_id": 0, "category_id": "", "category_name": "local",
            "check_flag": 1, "duration": _us(clip.duration), "effect_id": "",
            "formula_id": "", "id": material_id, "intensifies_path": "",
            "is_ai_clone_tone": False, "is_text_edit_overdub": False,
            "is_ugc": False, "local_material_id": "", "music_id": "",
            "name": clip.path.name, "path": _native_path(clip.path),
            "query": "", "request_id": "", "resource_id": "", "search_id": "",
            "source_platform": 0, "team_id": "", "text_id": "",
            "tone_category_id": "", "tone_category_name": "",
            "tone_effect_id": "", "tone_effect_name": "", "tone_platform": "",
            "tone_second_category_id": "", "tone_second_category_name": "",
            "tone_speaker": "", "tone_type": "", "type": "music",
            "video_id": "", "wave_points": [],
        }

    def _text_material(self, cue: SubtitleCue, style: SubtitleStyle, material_id: str) -> dict:
        content = json.dumps(
            {
                "styles": [
                    {
                        "fill": {
                            "content": {
                                "render_type": "solid",
                                "solid": {"color": hex_to_rgb(style.color)},
                            }
                        },
                        "font": {"id": "", "path": ""},
                        "range": [0, len(cue.text)],
                        "size": style.font_size,
                        "strokes": [
                            {
                                "content": {
                                    "render_type": "solid",
                                    "solid": {"color": hex_to_rgb(style.stroke_color)},
                                },
                                "width": style.stroke_width,
                            }
                        ],
                    }
                ],
                "text": cue.text,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return {
            "add_type": 0, "alignment": style.alignment, "background_alpha": 1.0,
            "background_color": "", "background_height": 0.14,
            "background_horizontal_offset": 0.0, "background_round_radius": 0.0,
            "background_style": 0, "background_vertical_offset": 0.0,
            "background_width": 0.14, "bold_width": 0.0, "border_alpha": 1.0,
            "border_color": style.stroke_color, "border_width": style.stroke_width,
            "check_flag": 7, "combo_info": {"text_templates": []},
            "content": content, "fixed_height": -1.0, "fixed_width": -1.0,
            "font_category_id": "", "font_category_name": "", "font_id": "",
            "font_name": "", "font_path": "", "font_resource_id": "",
            "font_size": float(style.font_size), "font_source_platform": 0,
            "font_team_id": "", "font_title": "none", "font_url": "", "fonts": [],
            "force_apply_line_max_width": False, "global_alpha": 1.0,
            "group_id": "", "has_shadow": False, "id": material_id,
            "initial_scale": 1.0, "inner_padding": -1.0, "is_rich_text": False,
            "italic_degree": 0, "ktv_color": "", "language": "", "layer_weight": 1,
            "letter_spacing": 0.0, "line_feed": 1,
            "line_max_width": style.line_max_width,
            "line_spacing": style.line_spacing,
            "multi_language_current": "none", "name": "", "original_size": [],
            "preset_category": "", "preset_category_id": "", "preset_has_set_alpha": False,
            "preset_index": 0, "preset_name": "", "recognize_task_id": "",
            "recognize_type": 0, "relevance_segment": [], "shadow_alpha": 0.9,
            "shadow_angle": -45.0, "shadow_color": "", "shadow_distance": 5.0,
            "shadow_point": {"x": 0.6, "y": -0.6}, "shadow_smoothing": 0.45,
            "shape_clip_x": False, "shape_clip_y": False, "style_name": "",
            "sub_type": 0, "subtitle_keywords": None, "text_alpha": 1.0,
            "text_color": style.color, "text_curve": None,
            "text_preset_resource_id": "", "text_size": 30,
            "text_to_audio_ids": [], "tts_auto_update": False, "type": "text",
            "typesetting": 0, "underline": False, "underline_offset": 0.22,
            "underline_width": 0.05, "use_effect_default_color": True,
            "words": {"end_time": [], "start_time": [], "text": []},
        }

    def _segment_extras(self, with_canvas: bool = True) -> list[str]:
        """세그먼트마다 필요한 부속 머티리얼. 빠지면 캡컷이 렌더를 못 한다.

        오디오 세그먼트는 canvas_color 가 필요 없다.
        """
        refs: list[str] = []
        speed_id = new_id()
        self._materials["speeds"].append(
            {"curve_speed": None, "id": speed_id, "mode": 0, "speed": 1.0, "type": "speed"}
        )
        refs.append(speed_id)

        if with_canvas:
            canvas_id = new_id()
            self._materials["canvases"].append(
                {"album_image": "", "blur": 0.0, "color": "", "id": canvas_id,
                 "image": "", "image_id": "", "image_name": "", "source_platform": 0,
                 "team_id": "", "type": "canvas_color"}
            )
            refs.append(canvas_id)

        mapping_id = new_id()
        self._materials["sound_channel_mappings"].append(
            {"audio_channel_mapping": 0, "id": mapping_id,
             "is_config_open": False, "type": "none"}
        )
        refs.append(mapping_id)

        vocal_id = new_id()
        self._materials["vocal_separations"].append(
            {"choice": 0, "id": vocal_id, "production_path": "",
             "removed_sounds": [], "time_range": None, "type": "vocal_separation"}
        )
        refs.append(vocal_id)
        return refs

    # --------------------------------------------------------------- timeline

    def add_clip(
        self,
        media: MediaInfo,
        source_start: float,
        length: float,
        volume: float = 1.0,
    ) -> None:
        """영상 트랙 끝에 클립 한 토막을 붙인다. 모드 B 의 B롤도 이걸 쓴다."""
        start_us = _us(source_start)
        length_us = _us(length)
        if length_us <= 0:
            return
        # 소스 끝을 넘어가는 요청은 캡컷에서 클립이 사라지는 원인이 된다.
        length_us = min(length_us, media.duration_us - start_us)
        if length_us <= 0:
            return
        material_id = self._register_video(media)
        self._segments.append(
            self._video_segment(
                material_id, start_us, length_us, self._video_cursor_us,
                len(self._segments), volume,
            )
        )
        self._video_cursor_us += length_us

    def add_video_cuts(self, keeps: list[Range]) -> None:
        """보존 구간들을 잘라 붙여 점프컷 타임라인을 만든다."""
        if self.media is None:
            raise ValueError("주 소스(media)가 없습니다. add_clip 을 쓰세요.")
        for keep in keeps:
            self.add_clip(self.media, keep.start, keep.length)

    def add_audio(
        self,
        path: Path,
        duration: float,
        target_start: float = 0.0,
        volume: float = 1.0,
        source_start: float = 0.0,
    ) -> None:
        """오디오 트랙에 한 토막. 나레이션이면 target_start 로 줄을 세운다."""
        clip = AudioClip(
            path=Path(path).expanduser().resolve(), duration=duration,
            target_start=target_start, volume=volume, source_start=source_start,
        )
        length_us = _us(clip.duration)
        if length_us <= 0:
            return
        material_id = new_id()
        self._materials["audios"].append(self._audio_material(clip, material_id))
        self._meta_entries.append(
            self._material_meta_audio(clip)
        )
        self._audio_segments.append({
            "cartoon": False,
            "clip": None,
            "common_keyframes": [],
            "enable_adjust": False, "enable_color_curves": True,
            "enable_color_wheels": True, "enable_lut": False,
            "extra_material_refs": self._segment_extras(with_canvas=False),
            "group_id": "", "hdr_settings": None, "id": new_id(),
            "intensifies_audio": False, "is_placeholder": False,
            "is_tone_modify": False, "keyframe_refs": [],
            "last_nonzero_volume": clip.volume, "material_id": material_id,
            "render_index": 0, "responsive_layout": {
                "enable": False, "horizontal_pos_layout": 0, "size_layout": 0,
                "target_follow": "", "vertical_pos_layout": 0,
            },
            "reverse": False,
            "source_timerange": {"duration": length_us, "start": _us(clip.source_start)},
            "speed": 1.0,
            "target_timerange": {"duration": length_us, "start": _us(clip.target_start)},
            "template_id": "", "template_scene": "default", "track_attribute": 0,
            "track_render_index": 0, "uniform_scale": None,
            "visible": True, "volume": clip.volume,
        })

    def add_subtitles(
        self, cues: list[SubtitleCue], style: SubtitleStyle | None = None
    ) -> None:
        """자막 트랙. 시간은 **타임라인(컷 적용 후)** 기준이어야 한다."""
        style = style or SubtitleStyle()
        for i, cue in enumerate(cues):
            if not cue.text.strip() or cue.duration <= 0:
                continue
            material_id = new_id()
            self._materials["texts"].append(
                self._text_material(cue, style, material_id)
            )
            self._text_segments.append(
                self._text_segment(cue, style, material_id, _TEXT_RENDER_BASE + i)
            )

    def _text_segment(
        self, cue: SubtitleCue, style: SubtitleStyle, material_id: str, render_index: int
    ) -> dict:
        return {
                "cartoon": False,
                "clip": {
                    "alpha": 1.0,
                    "flip": {"horizontal": False, "vertical": False},
                    "rotation": 0.0, "scale": {"x": 1.0, "y": 1.0},
                    # 지뢰 7번: 자막 위치는 여기다.
                    "transform": {"x": style.transform_x, "y": style.transform_y},
                },
                "common_keyframes": motion_keyframes(
                    style.motion, cue.duration, style.transform_y
                ),
                "enable_adjust": False,
                "enable_color_curves": True, "enable_color_wheels": True,
                "enable_lut": False, "extra_material_refs": [],
                "group_id": "", "hdr_settings": None, "id": new_id(),
                "intensifies_audio": False, "is_placeholder": False,
                "is_tone_modify": False, "keyframe_refs": [],
                "last_nonzero_volume": 1.0, "material_id": material_id,
                "render_index": render_index,
                "responsive_layout": {
                    "enable": False, "horizontal_pos_layout": 0, "size_layout": 0,
                    "target_follow": "", "vertical_pos_layout": 0,
                },
                "reverse": False,
                "source_timerange": None,
                "speed": 1.0,
                "target_timerange": {
                    "duration": _us(cue.duration), "start": _us(cue.start),
                },
                "template_id": "", "template_scene": "default", "track_attribute": 0,
                "track_render_index": render_index,
                "uniform_scale": {"on": True, "value": 1.0},
                "visible": True, "volume": 1.0,
            }

    def add_title(
        self,
        text: str,
        style: SubtitleStyle | None = None,
        start: float = 0.0,
        end: float | None = None,
    ) -> None:
        """영상을 대표하는 제목 한 장. 영상당 하나, 한 줄.

        말하는 자막과 **같은 텍스트 트랙에 섞지 않는다.** 위치가 달라
        한 트랙에 두면 캡컷 안에서 한꺼번에 옮기기가 불편해진다.
        """
        line = " ".join(text.split())
        if not line:
            return
        if end is None:
            end = max(self.timeline_us / US, start + 1.0)
        if end <= start:
            return
        assert self.canvas is not None
        style = style or SubtitleStyle.from_pixels(TITLE_Y_PX, self.canvas.height)
        cue = SubtitleCue(start, end, line)
        material_id = new_id()
        self._materials["texts"].append(self._text_material(cue, style, material_id))
        self._title_segments.append(
            self._text_segment(cue, style, material_id, _TITLE_RENDER_BASE)
        )

    def _video_segment(
        self, material_id: str, source_start_us: int, length_us: int,
        target_start_us: int, index: int, volume: float = 1.0,
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
            "is_tone_modify": False, "keyframe_refs": [],
            "last_nonzero_volume": volume,
            "material_id": material_id, "render_index": 0,
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
            "visible": True, "volume": volume,
        }

    # ---------------------------------------------------------------- writing

    def _tracks(self) -> list[dict]:
        def track(kind: str, segments: list[dict]) -> dict:
            return {
                "attribute": 0, "flag": 0, "id": new_id(), "is_default_name": True,
                "name": "", "segments": segments, "type": kind,
            }

        tracks = [track("video", self._segments)]
        if self._audio_segments:
            tracks.append(track("audio", self._audio_segments))
        if self._text_segments:
            tracks.append(track("text", self._text_segments))
        if self._title_segments:
            # 위치가 다르므로 말하는 자막과 트랙을 나눈다. 캡컷 안에서
            # 한쪽만 통째로 옮기거나 끄기 쉬워진다.
            tracks.append(track("text", self._title_segments))
        return tracks

    def timeline_json(self) -> dict:
        assert self.canvas is not None
        return {
            "canvas_config": {
                "height": self.canvas.height,
                "ratio": "original" if self.is_original_canvas
                else crop_ratio_label(self.canvas),
                "width": self.canvas.width,
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
            "cover": None, "create_time": 0, "duration": self.timeline_us,
            "extra_info": None, "fps": self.fps,
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
                {"type": 0, "value": self._stamped_meta_entries(now_us)},
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
            "tm_duration": self.timeline_us,
        }

    def _stamped_meta_entries(self, now_us: int) -> list[dict]:
        entries = []
        for entry in self._meta_entries:
            stamped = dict(entry)
            stamped["create_time"] = int(now_us / US)
            stamped["import_time"] = int(now_us / US)
            stamped["import_time_ms"] = now_us
            entries.append(stamped)
        return entries

    def _material_meta(self, media: MediaInfo, metetype: str) -> dict:
        return {
            "create_time": 0, "duration": media.duration_us,
            "extra_info": media.path.name,
            "file_Path": _native_path(media.path),
            "height": media.height, "id": new_id(),
            "import_time": 0, "import_time_ms": 0,
            "item_source": 1, "md5": "", "metetype": metetype,
            "roughcut_time_range": {"duration": media.duration_us, "start": 0},
            "sub_time_range": {"duration": -1, "start": -1},
            "type": 0, "width": media.width,
        }

    def _material_meta_audio(self, clip: AudioClip) -> dict:
        duration_us = _us(clip.duration)
        return {
            "create_time": 0, "duration": duration_us,
            "extra_info": clip.path.name,
            "file_Path": _native_path(clip.path),
            "height": 0, "id": new_id(),
            "import_time": 0, "import_time_ms": 0,
            "item_source": 1, "md5": "", "metetype": "music",
            "roughcut_time_range": {"duration": duration_us, "start": 0},
            "sub_time_range": {"duration": -1, "start": -1},
            "type": 0, "width": 0,
        }

    def _stage_media(self, folder: Path, mode: MediaMode) -> dict[str, str]:
        """미디어를 드래프트 폴더 안으로 들인다. {원본경로: 새경로} 를 돌려준다.

        지뢰 8번. macOS 캡컷이 샌드박스 밖 파일을 못 읽는다. 하드링크면 용량이
        늘지 않고, 다른 볼륨이라 하드링크가 안 되면 복사로 떨어진다.
        """
        if mode == "none":
            return {}

        materials = folder / MATERIALS_DIRNAME
        materials.mkdir(parents=True, exist_ok=True)
        mapping: dict[str, str] = {}
        used: set[str] = set()

        sources = [m["path"] for m in self._materials["videos"]]
        sources += [m["path"] for m in self._materials["audios"]]

        for raw in sources:
            if raw in mapping:
                continue
            source = Path(raw)
            if not source.is_file():
                # 원본이 없으면 손댈 게 없다. 경로를 그대로 둔다.
                continue
            name = source.name
            stem, suffix = source.stem, source.suffix
            counter = 1
            while name in used:
                name = f"{stem}-{counter}{suffix}"
                counter += 1
            used.add(name)
            target = materials / name
            mapping[raw] = _native_path(target)
            if target.exists():
                continue
            if mode in ("auto", "link"):
                try:
                    os.link(source, target)
                    continue
                except OSError:
                    if mode == "link":
                        raise
            shutil.copy2(source, target)
        return mapping

    def _rewrite_paths(self, mapping: dict[str, str]) -> None:
        for material in self._materials["videos"] + self._materials["audios"]:
            replacement = mapping.get(material["path"])
            if replacement:
                material["path"] = replacement
        for entry in self._meta_entries:
            replacement = mapping.get(entry["file_Path"])
            if replacement:
                entry["file_Path"] = replacement

    def write(self, root: Path, media_mode: MediaMode | None = None) -> Path:
        folder = root / self.name
        folder.mkdir(parents=True, exist_ok=True)

        mode = media_mode or default_media_mode()
        self._rewrite_paths(self._stage_media(folder, mode))

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
