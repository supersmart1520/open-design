"""FFmpeg 미리보기 렌더 — 캡컷 없이 결과를 눈으로 본다.

캡컷 드래프트는 캡컷에서만 열린다. 그런데 컷이 맞는지, 자막이 읽히는지,
제목이 얼굴을 가리지 않는지는 **보면 1초**다. 그래서 같은 데이터로 MP4 도 굽는다.

  한 가지 원칙: 드래프트와 MP4 는 **같은 컷·자막 데이터**에서 나온다.
  따로 계산하면 검수한 영상과 캡컷 프로젝트가 어긋난다.

자막은 ASS 로 굽는다. 한 줄 안에서 색과 크기를 바꿀 수 있어 제목 2색·2크기가
그대로 표현된다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .draft import SubtitleStyle
from .framing import Canvas, CropWindow, crop_window
from .media import FFMPEG, MediaInfo
from .silence import Range
from .subtitle import SubtitleCue
from .title import Title

#: 한글이 들어있는 폰트 후보. 앞에서부터 있는 걸 쓴다.
FONT_CANDIDATES = (
    "Pretendard", "Noto Sans CJK KR", "AppleSDGothicNeo", "Malgun Gothic",
    "NanumGothic", "WenQuanYi Zen Hei", "Unifont",
)

#: 캡컷 font_size 1 이 1920 높이 화면에서 차지하는 픽셀.
#: font_size 12 가 한 줄 18자로 읽히는 지점에 맞춰 잡았다.
PX_PER_FONT_UNIT = 4.1


def pick_font() -> str:
    """설치된 한글 폰트 하나. fontconfig 가 없으면 마지막 후보로."""
    try:
        listed = subprocess.run(
            ["fc-list", ":lang=ko", "family"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return FONT_CANDIDATES[-1]
    for candidate in FONT_CANDIDATES:
        if candidate.lower() in listed.lower():
            return candidate
    first = listed.strip().splitlines()
    return first[0].split(",")[0] if first else FONT_CANDIDATES[-1]


def ass_colour(hex_colour: str) -> str:
    """#RRGGBB → ASS 의 &HBBGGRR. ASS 는 BGR 순서다 — 뒤집지 않으면 색이 바뀐다."""
    text = hex_colour.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"#RRGGBB 형식이 아닙니다: {hex_colour}")
    red, green, blue = text[0:2], text[2:4], text[4:6]
    return f"&H00{blue}{green}{red}".upper()


def ass_time(seconds: float) -> str:
    total = max(seconds, 0.0)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours):d}:{int(minutes):02d}:{secs:05.2f}"


def to_ass_xy(transform_x: float, transform_y: float, canvas: Canvas) -> tuple[int, int]:
    """정규화 좌표(중앙 0, 위가 양수) → ASS 픽셀(좌상단 0,0)."""
    x = canvas.width / 2 * (1 + transform_x)
    y = canvas.height / 2 * (1 - transform_y)
    return int(round(x)), int(round(y))


def font_px(font_size: float, canvas: Canvas) -> int:
    return max(12, int(round(font_size * PX_PER_FONT_UNIT * canvas.height / 1920)))


@dataclass
class RenderPlan:
    """드래프트를 만든 것과 **같은** 재료."""

    media: MediaInfo
    keeps: list[Range]
    cues: list[SubtitleCue]
    canvas: Canvas
    speech_style: SubtitleStyle
    title: Title | None = None
    title_style: SubtitleStyle | None = None
    title_span: tuple[float, float] | None = None


def build_ass(plan: RenderPlan, font: str | None = None) -> str:
    """자막과 제목을 ASS 로. 시간은 **컷 적용 후** 기준이다."""
    font = font or pick_font()
    canvas = plan.canvas
    speech_px = font_px(plan.speech_style.font_size, canvas)
    outline = max(2, round(speech_px * plan.speech_style.stroke_width))

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {canvas.width}",
        f"PlayResY: {canvas.height}",
        "WrapStyle: 2",            # 줄바꿈은 우리가 이미 정했다. libass 가 건드리지 못하게.
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: speech,{font},{speech_px},"
        f"{ass_colour(plan.speech_style.color)},"
        f"{ass_colour(plan.speech_style.stroke_color)},&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},2,5,40,40,40,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text",
    ]

    events: list[str] = []
    speech_x, speech_y = to_ass_xy(
        plan.speech_style.transform_x, plan.speech_style.transform_y, canvas
    )
    for cue in plan.cues:
        text = cue.text.replace("\n", r"\N")
        events.append(
            f"Dialogue: 0,{ass_time(cue.start)},{ass_time(cue.end)},speech,,0,0,0,,"
            f"{{\\pos({speech_x},{speech_y})}}{text}"
        )

    if plan.title and plan.title_span:
        events.append(_title_event(plan, canvas))

    return "\n".join([*header, *events, ""])


def _title_event(plan: RenderPlan, canvas: Canvas) -> str:
    """제목 한 줄. 1행과 2행의 색·크기가 다르므로 인라인 태그로 바꿔 쓴다."""
    title = plan.title
    style = plan.title_style or plan.speech_style
    start, end = plan.title_span
    x, y = to_ass_xy(style.transform_x, style.transform_y, canvas)
    base = font_px(style.font_size, canvas)
    # 큰 글자는 테두리도 같이 굵어져야 배경에서 떨어진다.
    outline = max(3, round(base * style.stroke_width))
    accent = ass_colour(title.palette.accent)
    plain = ass_colour(title.palette.plain)

    body = f"{{\\c{accent}\\fs{base}\\bord{outline}}}{title.line1}"
    if title.line2:
        second = max(12, int(round(base * title.line2_scale)))
        body += f"\\N{{\\c{plain}\\fs{second}\\bord{outline}}}{title.line2}"
    return (
        f"Dialogue: 1,{ass_time(start)},{ass_time(end)},speech,,0,0,0,,"
        f"{{\\pos({x},{y})}}{body}"
    )


def select_expression(keeps: list[Range]) -> str:
    """보존 구간만 남기는 select 식. 컷을 영상에 그대로 적용한다."""
    return "+".join(
        f"between(t,{keep.start:.3f},{keep.end:.3f})" for keep in keeps
    ) or "1"


def crop_filter(window: CropWindow, media: MediaInfo) -> str:
    if window.is_full_frame:
        return ""
    width = (window.right - window.left) * media.width
    height = (window.bottom - window.top) * media.height
    x = window.left * media.width
    y = window.top * media.height
    return f"crop={int(width)}:{int(height)}:{int(x)}:{int(y)},"


def build_command(plan: RenderPlan, ass_path: Path, output: Path) -> list[str]:
    """ffmpeg 한 방. 컷 → 크롭 → 스케일 → 자막 순서다."""
    window = crop_window(
        plan.media.width, plan.media.height, plan.canvas, "center"
    )
    select = select_expression(plan.keeps)
    # ASS 경로의 콜론·역슬래시는 필터 문법과 충돌한다.
    escaped = str(ass_path).replace("\\", "/").replace(":", r"\:")

    video = (
        f"select='{select}',setpts=N/FRAME_RATE/TB,"
        f"{crop_filter(window, plan.media)}"
        f"scale={plan.canvas.width}:{plan.canvas.height}:force_original_aspect_ratio=decrease,"
        f"pad={plan.canvas.width}:{plan.canvas.height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"subtitles='{escaped}'"
    )
    audio = f"aselect='{select}',asetpts=N/SR/TB"

    return [
        FFMPEG, "-y", "-v", "error", "-i", str(plan.media.path),
        "-vf", video, "-af", audio,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
        str(output),
    ]


def render(plan: RenderPlan, output: Path, workdir: Path | None = None) -> Path:
    """미리보기 MP4 를 굽는다."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(workdir or output.parent)
    workdir.mkdir(parents=True, exist_ok=True)

    ass_path = workdir / f"{output.stem}.ass"
    ass_path.write_text(build_ass(plan), encoding="utf-8")
    try:
        subprocess.run(build_command(plan, ass_path, output), check=True)
    finally:
        ass_path.unlink(missing_ok=True)
    return output
