"""폴더 열기 — OS 별 명령과 실패 보고."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from capcut_agent.reveal import command_for, reveal


def test_command_per_os():
    assert command_for(Path("/a b"), "Darwin") == ["open", "/a b"]
    assert command_for(Path("/x"), "Windows") == ["explorer", "/x"]
    assert command_for(Path("/x"), "Linux") == ["xdg-open", "/x"]


def test_unknown_os_says_so(tmp_path):
    # 모르는 OS 에서 아무 명령이나 쏘지 않는다.
    with pytest.raises(RuntimeError, match="열 수 없습니다"):
        reveal(tmp_path, "Plan9")


def test_missing_folder_is_created_then_opened(tmp_path, monkeypatch):
    # 비어 있어도 열어주는 게 "없습니다" 보다 쓸모 있다.
    target = tmp_path / "inbox"
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["existed"] = target.is_dir()
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert reveal(target, "Darwin") == target
    assert seen["command"] == ["open", str(target)]
    assert seen["existed"] is True


def test_failure_carries_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 1, b"", "그런 건 없다".encode()),
    )
    with pytest.raises(RuntimeError, match="그런 건 없다"):
        reveal(tmp_path, "Linux")


def test_windows_explorer_returning_one_is_not_a_failure(tmp_path, monkeypatch):
    # explorer 는 멀쩡히 열고도 1 을 돌려준다. 종료 코드로 판단하면
    # 열린 창을 두고 실패했다고 말하게 된다.
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 1, b"", b""),
    )
    assert reveal(tmp_path, "Windows") == tmp_path
