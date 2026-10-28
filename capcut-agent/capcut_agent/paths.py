"""캡컷 드래프트 폴더 탐색.

함정 정리
  * macOS 최신 캡컷은 **샌드박스 컨테이너** 안에 드래프트를 둔다.
    ~/Movies/CapCut/... 만 보고 "없다"고 판단하면 안 된다.
  * 타임라인 파일 이름이 버전마다 다르다.
    Windows/구버전 → draft_content.json,  macOS 최신 → draft_info.json
    둘 중 무엇을 읽을지 알 수 없으므로 **양쪽 다** 같은 내용으로 쓴다.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

#: 타임라인 JSON 파일명. 캡컷 버전에 따라 읽는 이름이 다르므로 둘 다 기록한다.
TIMELINE_FILENAMES = ("draft_content.json", "draft_info.json")

_DRAFT_SUFFIX = Path("User Data") / "Projects" / "com.lveditor.draft"


def capcut_draft_roots(system: str | None = None) -> list[Path]:
    """이 OS에서 캡컷 드래프트 루트가 있을 법한 경로들을 우선순위대로."""
    system = system or platform.system()
    home = Path.home()
    roots: list[Path] = []

    if system == "Darwin":
        containers = home / "Library" / "Containers"
        roots += [
            # 샌드박스 (최신 CapCut 국제판)
            containers / "com.lemon.lvoverseas" / "Data" / "Movies" / "CapCut" / _DRAFT_SUFFIX,
            # 샌드박스 (剪映 국내판)
            containers / "com.lemon.jianying" / "Data" / "Movies" / "JianyingPro" / _DRAFT_SUFFIX,
            # 비샌드박스 / 구버전
            home / "Movies" / "CapCut" / _DRAFT_SUFFIX,
            home / "Movies" / "JianyingPro" / _DRAFT_SUFFIX,
        ]
    elif system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        roots += [
            local / "CapCut" / _DRAFT_SUFFIX,
            local / "JianyingPro" / _DRAFT_SUFFIX,
            roaming / "CapCut" / _DRAFT_SUFFIX,
        ]
    else:
        # 리눅스에 캡컷 데스크톱은 없다. 개발/테스트용 탈출구만 둔다.
        roots += [home / "CapCut" / _DRAFT_SUFFIX]

    override = os.environ.get("CAPCUT_DRAFT_DIR")
    if override:
        roots.insert(0, Path(override).expanduser())
    return roots


def resolve_draft_root(explicit: str | Path | None = None) -> Path:
    """실제로 존재하는 드래프트 루트 1개를 고른다. 없으면 설명과 함께 에러."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_dir():
            raise FileNotFoundError(f"지정한 드래프트 폴더가 없습니다: {path}")
        return path

    candidates = capcut_draft_roots()
    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    listed = "\n  ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        "캡컷 드래프트 폴더를 찾지 못했습니다. 캡컷을 설치하고 한 번 실행해 "
        "프로젝트를 1개 만들면 폴더가 생성됩니다.\n"
        f"찾아본 경로:\n  {listed}\n"
        "다른 위치에 있다면 CAPCUT_DRAFT_DIR 환경변수로 지정하세요."
    )
