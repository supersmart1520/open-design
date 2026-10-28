"""앱 창 — 어떤 방식으로 띄우는지.

`pywebview` → 크롬 계열 앱 모드 → 그냥 브라우저 탭. 창이 없는 프로세스는
macOS 가 독에서 치워 버려서, 사용자에게는 "실행이 안 된다" 로 보인다.
"""

from __future__ import annotations

import subprocess

from capcut_agent import desktop


def test_app_mode_picks_the_first_browser_present():
    command = desktop.app_mode_command(
        "http://127.0.0.1:8765", "Darwin", lambda path: "Brave" in path
    )
    assert command == [
        "open", "-na", "/Applications/Brave Browser.app",
        "--args", "--app=http://127.0.0.1:8765",
    ]


def test_app_mode_is_mac_only():
    # `--app=` 은 크롬 계열의 기능이고, 여기서 쓰는 `open` 은 맥 전용이다.
    assert desktop.app_mode_command("http://x", "Linux", lambda path: True) is None
    assert desktop.app_mode_command("http://x", "Windows", lambda path: True) is None


def test_app_mode_absent_when_no_browser_installed():
    assert desktop.app_mode_command("http://x", "Darwin", lambda path: False) is None


def test_open_window_falls_back_to_a_plain_tab(monkeypatch):
    # 앱 모드가 깨져도 대시보드는 열려야 한다. 기능은 같다.
    monkeypatch.setattr(desktop, "app_mode_command",
                        lambda url, *a, **k: ["open", "-na", "없는앱"])

    def explode(command, **kwargs):
        raise OSError("그런 앱이 없습니다")

    monkeypatch.setattr(subprocess, "run", explode)

    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.append(url))

    assert desktop.open_window("http://127.0.0.1:8765") == "browser"
    assert opened == ["http://127.0.0.1:8765"]


def test_open_window_reports_app_mode(monkeypatch):
    monkeypatch.setattr(desktop, "app_mode_command", lambda url, *a, **k: ["open", "x"])
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 0, b"", b""),
    )
    monkeypatch.setattr(
        desktop.webbrowser, "open",
        lambda url: (_ for _ in ()).throw(AssertionError("탭으로 떨어지면 안 됩니다")),
    )

    assert desktop.open_window("http://127.0.0.1:8765") == "app-window"


# --- 네이티브 창이 뜨지 못할 때 ------------------------------------------------
#
# pywebview 는 창을 못 띄워도 예외 없이 바로 돌아오는 경우가 있다. 그러면
# 앱이 코드 0 으로 끝나고, 아이콘만 깜빡이고 사라진다.


def test_window_that_dies_instantly_is_not_a_success(monkeypatch):
    monkeypatch.setattr(desktop, "open_native", lambda url: 0.2)
    assert desktop.open_window("http://x") == "window-failed"


def test_window_that_lived_is_a_success(monkeypatch):
    # 사람이 1~2초 만에 창을 닫을 일은 없다. 오래 열려 있었으면 정상이다.
    monkeypatch.setattr(desktop, "open_native", lambda url: 42.0)
    assert desktop.open_window("http://x") == "window"


def test_no_pywebview_goes_straight_to_fallback(monkeypatch):
    monkeypatch.setattr(desktop, "open_native", lambda url: None)
    monkeypatch.setattr(desktop, "open_fallback", lambda url: "app-window")
    assert desktop.open_window("http://x") == "app-window"


def test_run_falls_back_instead_of_exiting(monkeypatch, tmp_path):
    """창이 못 떠도 대시보드는 열려야 한다. 여기서 끝내면 아무 일도 안 일어난다."""
    monkeypatch.setattr(desktop, "open_window", lambda url: "window-failed")

    opened = []
    monkeypatch.setattr(desktop, "open_fallback", lambda url: opened.append(url) or "browser")
    monkeypatch.setattr(desktop, "wait_until_up", lambda port, **kw: True)
    monkeypatch.setattr(desktop.DashboardServer, "start", lambda self: None)
    monkeypatch.setattr(desktop.DashboardServer, "stop", lambda self: None)

    # 지켜보기만 하는 루프를 한 번 돌고 끊는다.
    def interrupt(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(desktop.time, "sleep", interrupt)

    said = []
    assert desktop.run(tmp_path, port=0, on_message=said.append) == 0
    assert len(opened) == 1, "다른 방법으로 열지 않았습니다"
    assert any(desktop.UP_MARKER in line for line in said), "표식이 없습니다"


# --- 독에 뜨는 이름 ------------------------------------------------------------
#
# venv 의 파이썬은 앱 번들이 아니다. 그냥 띄우면 macOS 가 "Python" 이라고
# 부른다. 사용자는 캡컷 에이전트를 찾는데 Python 이 떠 있으니 못 찾는다.


def test_branding_is_optional(monkeypatch):
    # AppKit 이 없는 곳(리눅스·CI)에서도 조용히 넘어가야 한다. 이름표는
    # 창보다 덜 중요하다 — 여기서 터지면 창조차 못 띄운다.
    assert desktop.brand_process() is False


def test_branding_uses_the_bundled_icon():
    assert desktop.ICON.is_file(), "아이콘이 패키지에 없습니다"
    assert desktop.ICON.read_bytes()[:4] == b"icns"


def test_native_window_is_branded_before_it_opens(monkeypatch):
    """창이 뜬 뒤에 이름을 바꾸면 이미 Python 으로 보인 뒤다."""
    import sys
    import types

    order = []

    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **kw: order.append("create")
    fake.start = lambda *a, **kw: order.append("start")
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(desktop, "brand_process",
                        lambda *a, **kw: order.append("brand") or True)

    desktop.open_native("http://x")

    assert order == ["brand", "create", "start"]
