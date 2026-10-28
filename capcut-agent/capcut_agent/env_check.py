"""Step 0 — OS 감지 + 환경 점검.

트랙 판정
  Darwin arm64     → A (Mac M칩, mlx-whisper)
  Windows AMD64    → C (faster-whisper, MP4 export 보너스)
  그 외            → fallback (faster-whisper)

빠진 도구는 설치 명령만 알려주고 멈춘다. 자동 설치하지 않는다.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .paths import capcut_draft_roots
from .storage import Workspace, describe

MIN_FREE_GB = 5.0

#: 요구 파이썬 버전.
#:
#: macOS 가 Xcode 와 함께 깔아주는 파이썬이 3.9 다. 거기에 mlx-whisper 를 깔고
#: 쓰는 게 가장 마찰이 적어, 바닥을 3.9 로 잡았다. 모든 모듈이
#: `from __future__ import annotations` 를 쓰므로 `X | None` 같은 표기도
#: 문자열로만 남아 3.9 에서 문제가 없다 (vermin 정적 분석 기준 실제 최소는 3.8).
MIN_PYTHON = (3, 9)

#: 드래프트 폴더가 없어도 일단 돌려볼 수 있는 점검 항목 이름.
DRAFT_CHECK_NAME = "CapCut 드래프트 폴더"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    system: str
    machine: str
    track: str
    track_label: str
    asr_backend: str
    checks: list[Check] = field(default_factory=list)
    workspace: Workspace | None = None

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


def detect_track(system: str, machine: str) -> tuple[str, str, str]:
    """(track, 라벨, ASR 백엔드) 반환."""
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "A", "Mac M칩 (mlx-whisper)", "mlx-whisper"
    if system == "Windows":
        return "C", "Windows (faster-whisper + MP4 export)", "faster-whisper"
    return "fallback", f"{system} {machine} (faster-whisper)", "faster-whisper"


def _ffmpeg_hint(system: str) -> str:
    if system == "Darwin":
        return "brew install ffmpeg"
    if system == "Windows":
        return "winget install --id Gyan.FFmpeg -e"
    return "sudo apt-get install -y ffmpeg"


def _python_hint(system: str) -> str:
    if system == "Darwin":
        return "brew install python@3.11"
    if system == "Windows":
        return "https://www.python.org/downloads/ 에서 3.11 설치"
    return "sudo apt-get install -y python3.11"


def _free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / (1024**3)
    except OSError:
        return 0.0


def _binary_version(binary: str) -> str:
    try:
        out = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=15)
        return out.stdout.splitlines()[0] if out.stdout else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


def run_checks(workspace: Workspace | None = None) -> Report:
    system = platform.system()
    machine = platform.machine()
    track, label, asr = detect_track(system, machine)
    report = Report(
        system=system, machine=machine, track=track, track_label=label,
        asr_backend=asr, workspace=workspace,
    )

    py = platform.python_version_tuple()
    py_ok = (int(py[0]), int(py[1])) >= MIN_PYTHON
    label = f"python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    report.checks.append(
        Check(label, py_ok, platform.python_version(),
              "" if py_ok else _python_hint(system))
    )

    if system == "Darwin":
        brew = shutil.which("brew")
        report.checks.append(
            Check("brew", brew is not None, brew or "",
                  "" if brew else '/bin/bash -c "$(curl -fsSL '
                  'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
        )

    for binary in ("ffmpeg", "ffprobe"):
        found = shutil.which(binary)
        report.checks.append(
            Check(binary, found is not None, _binary_version(binary) if found else "",
                  "" if found else _ffmpeg_hint(system))
        )

    roots = capcut_draft_roots(system)
    existing = [r for r in roots if r.is_dir()]
    report.checks.append(
        Check(DRAFT_CHECK_NAME, bool(existing),
              str(existing[0]) if existing else " | ".join(str(r) for r in roots),
              "" if existing else
              "CapCut 을 설치하고 한 번 실행해 프로젝트를 1개 만들어 주세요. "
              "폴더는 첫 실행 때 생성됩니다.")
    )

    free = _free_gb(workspace.home if workspace else Path.home())
    report.checks.append(
        Check(f"디스크 여유 {MIN_FREE_GB:.0f}GB+", free >= MIN_FREE_GB, f"{free:.1f}GB",
              "" if free >= MIN_FREE_GB else "디스크를 정리해 주세요.")
    )

    return report


def format_report(report: Report) -> str:
    lines = [
        f"OS        : {report.system} {report.machine}",
        f"트랙      : {report.track} — {report.track_label}",
        f"ASR       : {report.asr_backend}",
        "",
    ]
    if report.workspace is not None:
        lines.append(describe(report.workspace))
        lines.append("")
    for check in report.checks:
        mark = "OK  " if check.ok else "MISS"
        lines.append(f"[{mark}] {check.name}" + (f"  ({check.detail})" if check.detail else ""))
        if not check.ok and check.fix:
            lines.append(f"       → {check.fix}")
    lines.append("")
    lines.append("환경 준비 완료." if report.ok else "위 MISS 항목을 먼저 해결해 주세요.")
    return "\n".join(lines)
