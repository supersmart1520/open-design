"""작업 폴더를 OS 의 파일 관리자로 연다.

대시보드는 inbox 경로를 글자로만 보여줬다. 거기에 영상을 넣어야 하는데
폴더를 열 길이 없으면, 경로를 손으로 복사해 Finder 주소창에 붙이는 수밖에
없다. 버튼 하나로 끝낼 일이다.

**경로를 바깥에서 받지 않는다.** 대시보드는 `inbox` 같은 **이름**만 보내고
어떤 경로인지는 서버가 정한다. 로컬 전용이라도 열려 있는 브라우저 탭
하나가 이 서버로 요청을 보낼 수 있어서, 임의 경로를 여는 구멍을 만들지
않는다.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

#: OS 별 파일 관리자. 여기 없는 OS 면 열지 않고 그렇다고 말한다.
OPENERS = {
    "Darwin": "open",
    "Windows": "explorer",
    "Linux": "xdg-open",
}


def command_for(path: Path, system: str | None = None) -> list[str] | None:
    """`None` 이면 이 OS 에서는 열 방법을 모른다는 뜻."""
    opener = OPENERS.get(system or platform.system())
    return None if opener is None else [opener, str(path)]


def reveal(path: Path, system: str | None = None) -> Path:
    """폴더를 연다. 실패하면 예외를 올려 부른 쪽이 사용자에게 알리게 한다."""
    system = system or platform.system()
    command = command_for(path, system)
    if command is None:
        raise RuntimeError(f"이 OS 에서는 폴더를 열 수 없습니다: {system}")

    # 아직 안 만들어진 폴더를 열라고 할 수 있다. 비어 있어도 열어주는 게
    # "없습니다" 보다 쓸모 있다 — 사용자는 거기에 영상을 넣으려는 것이다.
    path.mkdir(parents=True, exist_ok=True)

    done = subprocess.run(command, capture_output=True, timeout=20)
    # Windows 의 explorer 는 성공해도 1 을 돌려준다. 종료 코드로 판단하면
    # 멀쩡히 열린 창을 두고 실패했다고 말하게 된다.
    if done.returncode != 0 and system != "Windows":
        detail = done.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(detail or f"{command[0]} 가 {done.returncode} 로 끝났습니다")
    return path
