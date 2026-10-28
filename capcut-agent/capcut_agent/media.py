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
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool

    @property
    def duration_us(self) -> int:
        return int(round(self.duration * 1_000_000))


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


def probe(video: str | Path) -> MediaInfo:
    path = Path(video).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")

    proc = _run_ok(
        [FFPROBE, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)]
    )
    data = json.loads(proc.stdout)
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

    return MediaInfo(
        path=path,
        width=int(video_stream.get("width") or 1920),
        height=int(video_stream.get("height") or 1080),
        fps=round(fps, 3),
        duration=duration,
        has_audio=audio_stream is not None,
    )


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
