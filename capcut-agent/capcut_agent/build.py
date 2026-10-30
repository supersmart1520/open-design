"""지금 **어느 코드가 돌고 있는지** 알려 준다.

이게 없으면 "기능이 안 된다" 를 풀 수 없다. 고쳐서 올려도, 받지 않았거나
앱을 다시 띄우지 않았으면 옛 코드가 계속 돈다. 겉모습은 똑같다. 그래서
버전과 커밋을 화면에 띄워, 그 한 줄로 가려낸다.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=4)
def revision(root: str = "") -> str:
    """짧은 커밋 해시. 고친 게 남아 있으면 `+` 를 붙인다.

    저장소가 아니거나 git 이 없으면 빈 문자열 — 없는 걸 지어내지 않는다.
    상태 조회마다 git 을 부르지 않게 결과를 기억해 둔다.
    """
    where = Path(root) if root else ROOT
    try:
        done = subprocess.run(
            ["git", "-C", str(where), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if done.returncode != 0:
            return ""
        short = done.stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(where), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        return short + ("+" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return ""


def describe() -> str:
    """사람이 읽을 한 줄. 화면과 터미널이 같은 문구를 쓰게 한다."""
    commit = revision()
    return f"v{__version__} ({commit})" if commit else f"v{__version__}"
