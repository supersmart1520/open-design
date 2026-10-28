"""inbox 자동 감지 — 복사가 끝나기 전에 등록하면 반쪽 파일을 편집한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from capcut_agent import storage
from capcut_agent.web.watcher import STABLE_CHECKS, InboxWatcher


@pytest.fixture
def workspace(tmp_path: Path):
    return storage.resolve_workspace(tmp_path / "ws")


@pytest.fixture
def seen():
    return []


@pytest.fixture
def watcher(workspace, seen):
    return InboxWatcher(workspace, lambda paths: seen.extend(paths))


def _sweep_until_settled(watcher) -> list[Path]:
    for _ in range(STABLE_CHECKS + 1):
        found = watcher.sweep()
        if found:
            return found
    return []


def test_a_stable_file_is_registered(workspace, watcher, seen):
    (workspace.inbox / "a.mp4").write_bytes(b"x" * 100)
    assert [p.name for p in _sweep_until_settled(watcher)] == ["a.mp4"]
    assert [p.name for p in seen] == ["a.mp4"]


def test_a_file_still_being_copied_is_not_registered(workspace, watcher, seen):
    """크기가 자라는 동안은 건드리지 않는다."""
    video = workspace.inbox / "big.mp4"
    for size in (100, 500, 900):
        video.write_bytes(b"x" * size)
        assert watcher.sweep() == []
    assert seen == []


def test_the_file_is_registered_once_copying_finishes(workspace, watcher, seen):
    video = workspace.inbox / "big.mp4"
    video.write_bytes(b"x" * 100)
    watcher.sweep()
    video.write_bytes(b"x" * 900)      # 아직 복사 중
    watcher.sweep()
    assert [p.name for p in _sweep_until_settled(watcher)] == ["big.mp4"]


def test_a_registered_file_is_not_reported_twice(workspace, watcher, seen):
    (workspace.inbox / "a.mp4").write_bytes(b"x" * 100)
    _sweep_until_settled(watcher)
    assert watcher.sweep() == []
    assert len(seen) == 1


def test_an_empty_file_is_never_registered(workspace, watcher, seen):
    (workspace.inbox / "empty.mp4").write_bytes(b"")
    for _ in range(4):
        assert watcher.sweep() == []


def test_non_video_files_are_ignored(workspace, watcher, seen):
    (workspace.inbox / "notes.txt").write_bytes(b"x" * 100)
    for _ in range(4):
        assert watcher.sweep() == []


def test_removing_and_re_adding_a_file_registers_it_again(workspace, watcher, seen):
    video = workspace.inbox / "a.mp4"
    video.write_bytes(b"x" * 100)
    _sweep_until_settled(watcher)
    video.unlink()
    watcher.sweep()
    video.write_bytes(b"x" * 100)
    assert [p.name for p in _sweep_until_settled(watcher)] == ["a.mp4"]
    assert len(seen) == 2


def test_an_empty_inbox_reports_nothing(watcher):
    assert watcher.sweep() == []


def test_a_missing_inbox_does_not_crash_the_loop(workspace, watcher):
    import shutil

    shutil.rmtree(workspace.inbox)
    assert watcher.sweep() == []
