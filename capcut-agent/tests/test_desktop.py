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
