"""ffmpeg / ffprobe 래퍼. 소스 영상에 대해 아는 모든 사실은 여기서 나온다."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


class ToolMissing(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: Path
    #: **표시** 기준 가로/세로. 회전 메타데이터를 이미 반영한 값이다.
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    #: 컨테이너에 적힌 회전각(도). 0 / 90 / 180 / 270.
    rotation: int = 0

    @property
    def duration_us(self) -> int:
        return int(round(self.duration * 1_000_000))

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ToolMissing(f"`{args[0]}` 를 찾을 수 없습니다. FFmpeg 를 설치하세요.") from exc


def _run_ok(args: list[str]) -> subprocess.CompletedProcess[str]:
    proc = _run(args)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"{args[0]} 실패 (exit {proc.returncode}):\n{tail}")
    return proc


def _rational(value: object) -> float:
    if not isinstance(value, str) or "/" not in value:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
    num, _, den = value.partition("/")
    try:
        n, d = float(num), float(den)
    except ValueError:
        return 0.0
    return n / d if d else 0.0


def read_rotation(video_stream: dict) -> int:
    """컨테이너의 회전각을 읽는다. 0 / 90 / 180 / 270 으로 정규화한다.

    ffprobe 가 회전을 알려주는 방식이 두 가지다.
      * 예전: streams[].tags.rotate = "90"
      * 요즘: streams[].side_data_list[].rotation = -90  (Display Matrix)
    Display Matrix 의 부호는 반시계가 음수라 화면 기준 각도와 반대다.
    아이폰 세로 촬영 .MOV 가 정확히 이 경우고, 이걸 놓치면 1080x1920 영상을
    1920x1080 으로 보게 되어 캔버스 비율이 통째로 뒤집힌다.
    """
    tags = video_stream.get("tags") or {}
    raw = tags.get("rotate")
    if raw is not None:
        try:
            return int(float(raw)) % 360
        except (TypeError, ValueError):
            pass

    for side_data in video_stream.get("side_data_list") or []:
        if "rotation" not in side_data:
            continue
        try:
            # Display Matrix 는 반시계 음수 → 화면 회전각으로 뒤집는다.
            return int(round(-float(side_data["rotation"]))) % 360
        except (TypeError, ValueError):
            continue
    return 0


def media_info_from_probe(data: dict, path: Path) -> MediaInfo:
    """ffprobe JSON → MediaInfo. 순수 함수라 단위테스트가 가능하다."""
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video_stream is None:
        raise RuntimeError(f"비디오 트랙이 없습니다: {path}")

    fps = (
        _rational(video_stream.get("avg_frame_rate"))
        or _rational(video_stream.get("r_frame_rate"))
        or 30.0
    )
    duration = float(
        data.get("format", {}).get("duration") or video_stream.get("duration") or 0.0
    )
    if duration <= 0:
        raise RuntimeError(f"길이를 읽지 못했습니다: {path}")

    width = int(video_stream.get("width") or 1920)
    height = int(video_stream.get("height") or 1080)
    rotation = read_rotation(video_stream)
    if rotation in (90, 270):
        # 세로로 찍은 아이폰 영상. 표시 기준으로는 가로/세로가 뒤바뀐다.
        width, height = height, width

    return MediaInfo(
        path=path,
        width=width,
        height=height,
        fps=round(fps, 3),
        duration=duration,
        has_audio=audio_stream is not None,
        rotation=rotation,
    )


def probe(video: str | Path) -> MediaInfo:
    path = Path(video).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")

    proc = _run_ok(
        [FFPROBE, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)]
    )
    return media_info_from_probe(json.loads(proc.stdout), path)


def detect_silence_raw(video: str | Path, noise_db: float, min_dur: float) -> str:
    """silencedetect 의 stderr 원문. 파싱은 silence.py 가 한다."""
    proc = _run_ok(
        [FFMPEG, "-nostats", "-i", str(video),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
         "-f", "null", "-"]
    )
    return proc.stderr


def extract_wav(video: str | Path, out_wav: str | Path) -> Path:
    """16kHz 모노 16bit — whisper 계열이 원하는 형식. 3단에서 쓴다."""
    out = Path(out_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run_ok(
        [FFMPEG, "-y", "-i", str(video), "-vn",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)]
    )
    return out
