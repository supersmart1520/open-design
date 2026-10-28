"""작업 저장소 — 원본 클립을 던져놓는 곳, 그리고 결과가 쌓이는 곳.

    ~/CapCutAgent/
        inbox/        ← 원본 mp4/mov 를 여기 던진다
        done/         ← 처리에 성공한 원본이 옮겨진다
        failed/       ← 처리에 실패한 원본이 옮겨진다
        reports/      ← 컷 계획 JSON (무엇을 왜 잘랐는지의 기록)
        config.json   ← 기본 컷 옵션. 매번 플래그 치지 않아도 되게.

위치는 `CAPCUT_AGENT_HOME` 환경변수나 `--workspace` 로 바꾼다.

완성된 드래프트는 **여기 두지 않는다.** 캡컷이 읽어야 하므로 캡컷 자신의
드래프트 루트에 직접 쓴다 (paths.py 참고). 저장소는 입력과 기록만 맡는다.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .silence import SilenceOptions

#: 저장소 기본 위치. 홈 아래 한 곳으로 모아 OS 상관없이 같은 모양이 되게 한다.
DEFAULT_HOME_NAME = "CapCutAgent"

#: inbox 에서 집어올 확장자.
VIDEO_EXTS = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"})


@dataclass(frozen=True)
class Workspace:
    home: Path

    @property
    def inbox(self) -> Path:
        return self.home / "inbox"

    @property
    def done(self) -> Path:
        return self.home / "done"

    @property
    def failed(self) -> Path:
        return self.home / "failed"

    @property
    def reports(self) -> Path:
        return self.home / "reports"

    @property
    def scripts(self) -> Path:
        """대본과 숏폼 후보 교환 파일. 사람이 열어 고치는 곳."""
        return self.home / "scripts"

    @property
    def broll(self) -> Path:
        """모드 B 가 쓸 B롤 클립. 여기 넣은 건 처리 후에도 이동하지 않는다."""
        return self.home / "broll"

    @property
    def voice(self) -> Path:
        """합성된 나레이션 mp3. 문장 해시로 캐시된다."""
        return self.home / "voice"

    @property
    def asr_cache(self) -> Path:
        """전사 캐시. 키는 파일 내용 해시."""
        return self.home / "cache" / "asr"

    @property
    def config_path(self) -> Path:
        return self.home / "config.json"

    @property
    def folders(self) -> tuple[Path, ...]:
        return (
            self.inbox, self.done, self.failed, self.reports,
            self.scripts, self.broll, self.voice, self.asr_cache,
        )


def default_home() -> Path:
    override = os.environ.get("CAPCUT_AGENT_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_HOME_NAME


def resolve_workspace(explicit: str | Path | None = None, create: bool = True) -> Workspace:
    """저장소를 정하고, 필요하면 폴더 구조를 만든다."""
    home = Path(explicit).expanduser() if explicit else default_home()
    workspace = Workspace(home=home)
    if create:
        for folder in workspace.folders:
            folder.mkdir(parents=True, exist_ok=True)
        ensure_config(workspace)
    return workspace


def ensure_config(workspace: Workspace) -> Path:
    """config.json 이 없으면 기본값으로 만든다. 있으면 건드리지 않는다."""
    if not workspace.config_path.exists():
        payload = {
            "_주석": "컷 옵션 기본값. CLI 플래그를 주면 그쪽이 이깁니다.",
            **SilenceOptions().to_dict(),
        }
        workspace.config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return workspace.config_path


def load_options(workspace: Workspace) -> SilenceOptions:
    """config.json 을 읽어 SilenceOptions 로. 깨졌거나 없으면 기본값."""
    defaults = SilenceOptions()
    try:
        raw = json.loads(workspace.config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(raw, dict):
        return defaults

    known = defaults.to_dict()
    values: dict[str, float] = {}
    for key in known:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values[key] = float(value)
    return SilenceOptions(**{**known, **values})


def list_inbox(workspace: Workspace) -> list[Path]:
    """inbox 안의 영상 파일. 이름 순으로 돌려 처리 순서가 예측 가능하게."""
    if not workspace.inbox.is_dir():
        return []
    found = [
        path
        for path in workspace.inbox.iterdir()
        if path.is_file()
        and path.suffix.lower() in VIDEO_EXTS
        # 맥의 ._foo.mp4 같은 리소스 포크 쓰레기를 집지 않는다.
        and not path.name.startswith((".", "~"))
    ]
    return sorted(found, key=lambda p: p.name.lower())


def list_broll(workspace: Workspace) -> list[Path]:
    """broll 폴더의 클립. 모드 B 가 화면에 깔 소스. 이동하지 않는다."""
    if not workspace.broll.is_dir():
        return []
    found = [
        path
        for path in workspace.broll.iterdir()
        if path.is_file()
        and path.suffix.lower() in VIDEO_EXTS
        and not path.name.startswith((".", "~"))
    ]
    return sorted(found, key=lambda p: p.name.lower())


def _unique(target: Path) -> Path:
    """같은 이름이 이미 있으면 -1, -2 를 붙여 덮어쓰기를 막는다."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for n in range(1, 1000):
        candidate = target.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"비어있는 이름을 찾지 못했습니다: {target}")


def archive(workspace: Workspace, video: Path, ok: bool = True) -> Path:
    """처리한 원본을 done/ 또는 failed/ 로 옮긴다.

    inbox 밖에 있는 파일(사용자가 경로로 직접 지정한 경우)은 건드리지 않는다.
    """
    video = video.resolve()
    if video.parent != workspace.inbox.resolve():
        return video
    destination = _unique((workspace.done if ok else workspace.failed) / video.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(video), str(destination))
    return destination


def write_report(workspace: Workspace, stem: str, payload: dict) -> Path:
    """컷 계획을 JSON 으로 남긴다. 나중에 왜 그렇게 잘렸는지 따질 근거."""
    workspace.reports.mkdir(parents=True, exist_ok=True)
    path = _unique(workspace.reports / f"{stem}.json")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def describe(workspace: Workspace) -> str:
    lines = [f"저장소    : {workspace.home}"]
    for label, folder in (
        ("  inbox  ", workspace.inbox),
        ("  broll  ", workspace.broll),
        ("  done   ", workspace.done),
        ("  failed ", workspace.failed),
        ("  scripts", workspace.scripts),
        ("  voice  ", workspace.voice),
        ("  reports", workspace.reports),
    ):
        count = len(list(folder.iterdir())) if folder.is_dir() else 0
        mark = "" if folder.is_dir() else "  (없음)"
        lines.append(f"{label} : {folder}  [{count}개]{mark}")
    lines.append(f"  config : {workspace.config_path}")
    return "\n".join(lines)
