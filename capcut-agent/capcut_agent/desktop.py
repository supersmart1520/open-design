"""PC 앱 — 대시보드를 창 하나로 띄운다.

터미널을 열고 명령을 치는 대신 아이콘을 더블클릭하면 되게 한다.

구조는 단순하다. 로컬 서버를 백그라운드 스레드로 올리고, 그 주소를 네이티브
창에 띄운다. 창을 닫으면 서버도 내려간다.

`pywebview` 가 있으면 네이티브 창을, 없으면 기본 브라우저를 연다. 둘 다
같은 대시보드이므로 기능 차이는 없다 — 창이 더 앱처럼 보일 뿐이다.
"""

from __future__ import annotations

import platform
import socket
import subprocess
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

#: 네이티브 창이 이보다 빨리 닫히면 띄우지 못한 것으로 본다. 사람이
#: 1~2초 만에 창을 닫을 일은 없다.
MIN_WINDOW_SECONDS = 3.0

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


#: 크롬 계열은 `--app=` 으로 탭도 주소창도 없는 창을 띄운다. 추가 설치
#: 없이 앱처럼 보이는 창을 얻는 유일한 길이다. 사파리에는 없는 기능이다.
APP_MODE_BROWSERS = (
    "/Applications/Google Chrome.app",
    "/Applications/Microsoft Edge.app",
    "/Applications/Brave Browser.app",
    "/Applications/Chromium.app",
)


def app_mode_command(url: str, system: str | None = None,
                     exists=None) -> list[str] | None:
    """탭 없는 창으로 띄우는 명령. 쓸 수 있는 브라우저가 없으면 `None`."""
    if (system or platform.system()) != "Darwin":
        return None
    seen = exists or (lambda path: Path(path).is_dir())
    for app in APP_MODE_BROWSERS:
        if seen(app):
            return ["open", "-na", app, "--args", f"--app={url}"]
    return None


def open_fallback(url: str) -> str:
    """창 없이 여는 길. 크롬 계열 앱 모드 → 그냥 브라우저 탭."""
    command = app_mode_command(url)
    if command is not None:
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=20)
            return "app-window"
        except (OSError, subprocess.SubprocessError):
            pass  # 브라우저 탭으로 떨어진다. 기능은 같다.
    webbrowser.open(url)
    return "browser"


#: 번들에 같이 넣는 아이콘. 독에 띄울 때도 같은 걸 쓴다.
ICON = Path(__file__).resolve().parent / "assets" / "appicon.icns"


def brand_process(title: str = WINDOW_TITLE, icon: Path = ICON) -> bool:
    """독과 메뉴 막대에 이름과 아이콘을 입힌다.

    venv 의 파이썬은 앱 번들이 아니다. 그래서 GUI 를 띄우면 macOS 가
    파이썬 프레임워크를 앱으로 보고 **"Python"** 이라고 부른다. 사용자는
    캡컷 에이전트를 찾는데 Python 이 떠 있으니 "안 열린다" 가 된다.

    실패해도 창은 뜬다. 이름표는 창보다 덜 중요하므로 조용히 넘어간다.
    """
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
    except ImportError:
        return False

    try:
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = title
            info["CFBundleDisplayName"] = title

        if icon.is_file():
            image = NSImage.alloc().initWithContentsOfFile_(str(icon))
            if image is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:
        return False
    return True


def open_native(url: str) -> float | None:
    """`pywebview` 로 창을 띄우고 **열려 있던 시간**을 돌려준다.

    `None` 은 pywebview 가 없다는 뜻. 띄우지 못했는데도 예외 없이 바로
    돌아오는 경우가 있어서, 성공/실패 대신 시간을 돌려준다 — 부른 쪽이
    "닫은 것" 과 "뜨지 못한 것" 을 가를 수 있게.
    """
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        return None

    brand_process()

    started = time.monotonic()
    try:
        webview.create_window(
            WINDOW_TITLE, url,
            width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=(720, 560),
        )
        webview.start()
    except Exception:
        # 창을 못 띄웠다고 앱이 끝나면 안 된다. 다른 길이 남아 있다.
        return 0.0
    return time.monotonic() - started


def open_window(url: str) -> str:
    """창을 연다. 좋은 쪽부터 차례로 떨어진다.

    `pywebview` → 크롬 계열 앱 모드 → 그냥 브라우저 탭. 어느 쪽으로
    열었는지 돌려준다 — 사용자에게 알려주기 위해서다.
    """
    lived = open_native(url)
    if lived is None:
        return open_fallback(url)
    if lived < MIN_WINDOW_SECONDS:
        # 띄우지 못했다. 여기서 끝내면 아이콘만 깜빡이고 사라진다.
        return "window-failed"
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

    try:
        how = open_window(server.url)
        if how == "window-failed":
            on_message("네이티브 창이 뜨지 못했습니다. 다른 방법으로 엽니다.")
            how = open_fallback(server.url)

        if how != "window":
            on_message("")
            if how == "app-window":
                on_message("탭 없는 창으로 열었습니다.")
            else:
                on_message(f"브라우저로 열었습니다: {server.url}")
            # 지금 돌고 있는 파이썬을 적는다. venv 안이면 그 경로여야 한다.
            on_message(f"앱 자체 창으로 띄우려면:  {sys.executable} -m pip install pywebview")
            on_message("종료하려면 Ctrl+C")
            on_message(UP_MARKER)
            # pywebview 가 아니면 창 닫힘 신호가 오지 않는다. 사용자가 끊을
            # 때까지 띄워 둔다 — 여기서 끝내면 서버도 같이 내려간다.
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0
