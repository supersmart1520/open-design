"""PC 앱 — 대시보드를 창 하나로 띄운다.

터미널을 열고 명령을 치는 대신 아이콘을 더블클릭하면 되게 한다.

구조는 단순하다. 로컬 서버를 백그라운드 스레드로 올리고, 그 주소를 네이티브
창에 띄운다. 창을 닫으면 서버도 내려간다.

`pywebview` 가 있으면 네이티브 창을, 없으면 기본 브라우저를 연다. 둘 다
같은 대시보드이므로 기능 차이는 없다 — 창이 더 앱처럼 보일 뿐이다.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

#: 서버가 뜰 때까지 기다리는 한계 (초).
STARTUP_TIMEOUT = 20.0

#: 기록에 남기는 표식. 런처가 이걸 보고 "떴다가 닫힌 것" 과 "뜨지도 못한
#: 것" 을 가른다. 창을 금방 닫는 건 잘못이 아니므로 알리면 안 된다.
UP_MARKER = "__CAPCUT_APP_UP__"

WINDOW_TITLE = "캡컷 에이전트"
WINDOW_SIZE = (1080, 820)


def free_port(preferred: int = 8765) -> int:
    """쓸 수 있는 포트. 선호 포트가 막혀 있으면 아무 빈 포트나."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return probe.getsockname()[1]
    raise RuntimeError("빈 포트를 찾지 못했습니다")


def wait_until_up(port: int, timeout: float = STARTUP_TIMEOUT) -> bool:
    """서버가 응답할 때까지 기다린다. 창을 빈 화면으로 띄우지 않기 위해서."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


class DashboardServer:
    """uvicorn 을 백그라운드 스레드에서 돌린다."""

    def __init__(self, workspace: Path, port: int, draft_dir: str | None = None):
        self.workspace = Path(workspace)
        self.port = port
        self.draft_dir = draft_dir
        self._server = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        import uvicorn

        from .web.app import create_app

        config = uvicorn.Config(
            create_app(self.workspace, self.draft_dir),
            host="127.0.0.1", port=self.port, log_level="warning",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, name="capcut-dashboard", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def open_window(url: str) -> str:
    """네이티브 창을 연다. pywebview 가 없으면 브라우저로 떨어진다.

    어느 쪽으로 열었는지 돌려준다 — 사용자에게 알려주기 위해서다.
    """
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        webbrowser.open(url)
        return "browser"

    webview.create_window(
        WINDOW_TITLE, url,
        width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=(720, 560),
    )
    webview.start()
    return "window"


def run(workspace: Path, draft_dir: str | None = None,
        port: int = 8765, on_message=print) -> int:
    """앱 실행. 창이 닫히면 서버를 내리고 끝난다."""
    chosen = free_port(port)
    server = DashboardServer(workspace, chosen, draft_dir)
    on_message(f"저장소    : {workspace}")
    on_message(f"대시보드  : {server.url}")
    server.start()

    if not wait_until_up(chosen):
        server.stop()
        on_message("서버가 뜨지 않았습니다.")
        return 1

    on_message(UP_MARKER)

    try:
        how = open_window(server.url)
        if how == "browser":
            on_message("")
            on_message("네이티브 창 대신 브라우저로 열었습니다.")
            # 지금 돌고 있는 파이썬을 적는다. venv 안이면 그 경로여야 한다.
            on_message(f"창으로 띄우려면 (선택):  {sys.executable} -m pip install pywebview")
            on_message("  — 빌드가 깨지면 그냥 두세요. 기능은 같습니다.")
            on_message("종료하려면 Ctrl+C")
            # 브라우저로 열었으면 창 닫힘 신호가 없다. 사용자가 끊을 때까지 둔다.
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0
