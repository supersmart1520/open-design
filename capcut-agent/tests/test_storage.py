"""작업 저장소 — 폴더 구조, config 병합, 아카이브, 기록."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capcut_agent import storage
from capcut_agent.silence import SilenceOptions


@pytest.fixture
def ws(tmp_path: Path) -> storage.Workspace:
    return storage.resolve_workspace(tmp_path / "CapCutAgent")


def test_resolve_creates_the_whole_folder_structure(ws):
    for folder in ws.folders:
        assert folder.is_dir()
    assert ws.config_path.is_file()


def test_default_home_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CAPCUT_AGENT_HOME", str(tmp_path / "elsewhere"))
    assert storage.default_home() == tmp_path / "elsewhere"


def test_default_home_without_env_sits_under_home(monkeypatch):
    monkeypatch.delenv("CAPCUT_AGENT_HOME", raising=False)
    assert storage.default_home() == Path.home() / storage.DEFAULT_HOME_NAME


def test_default_config_holds_the_shipped_defaults(ws):
    raw = json.loads(ws.config_path.read_text(encoding="utf-8"))
    for key, value in SilenceOptions().to_dict().items():
        assert raw[key] == value


def test_ensure_config_does_not_clobber_edits(ws):
    ws.config_path.write_text(json.dumps({"noise_db": -45.0}), encoding="utf-8")
    storage.ensure_config(ws)
    assert json.loads(ws.config_path.read_text(encoding="utf-8")) == {"noise_db": -45.0}


def test_load_options_applies_config_over_defaults(ws):
    ws.config_path.write_text(json.dumps({"noise_db": -45.0, "padding": 0.2}), encoding="utf-8")
    options = storage.load_options(ws)
    assert options.noise_db == -45.0
    assert options.padding == 0.2
    # 적지 않은 값은 기본값을 유지한다.
    assert options.min_keep == SilenceOptions().min_keep


def test_load_options_survives_a_broken_config(ws):
    ws.config_path.write_text("{ this is not json", encoding="utf-8")
    assert storage.load_options(ws) == SilenceOptions()


def test_load_options_ignores_non_numeric_values(ws):
    ws.config_path.write_text(json.dumps({"noise_db": "아주 조용히"}), encoding="utf-8")
    assert storage.load_options(ws).noise_db == SilenceOptions().noise_db


def test_list_inbox_picks_videos_in_name_order(ws):
    for name in ("b.mp4", "a.MOV", "notes.txt", ".DS_Store", "._ghost.mp4"):
        (ws.inbox / name).write_bytes(b"x")
    assert [p.name for p in storage.list_inbox(ws)] == ["a.MOV", "b.mp4"]


def test_archive_moves_an_inbox_file_to_done(ws):
    video = ws.inbox / "talk.mp4"
    video.write_bytes(b"x")
    moved = storage.archive(ws, video, ok=True)
    assert moved == ws.done / "talk.mp4"
    assert moved.is_file() and not video.exists()


def test_archive_sends_failures_to_failed(ws):
    video = ws.inbox / "talk.mp4"
    video.write_bytes(b"x")
    assert storage.archive(ws, video, ok=False) == ws.failed / "talk.mp4"


def test_archive_never_overwrites_an_existing_name(ws):
    (ws.done / "talk.mp4").write_bytes(b"old")
    video = ws.inbox / "talk.mp4"
    video.write_bytes(b"new")
    moved = storage.archive(ws, video)
    assert moved == ws.done / "talk-1.mp4"
    assert (ws.done / "talk.mp4").read_bytes() == b"old"


def test_archive_leaves_files_outside_the_inbox_alone(ws, tmp_path):
    outside = tmp_path / "somewhere" / "talk.mp4"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    assert storage.archive(ws, outside) == outside.resolve()
    assert outside.is_file()


def test_write_report_lands_in_reports_as_readable_json(ws):
    path = storage.write_report(ws, "talk-1030", {"결과": {"컷수": 3}})
    assert path.parent == ws.reports
    assert json.loads(path.read_text(encoding="utf-8"))["결과"]["컷수"] == 3


def test_write_report_does_not_overwrite(ws):
    first = storage.write_report(ws, "talk", {"n": 1})
    second = storage.write_report(ws, "talk", {"n": 2})
    assert first != second
    assert json.loads(first.read_text(encoding="utf-8"))["n"] == 1


# --------------------------------------------------- 파이썬 버전 바닥


def test_the_python_floor_matches_what_macos_ships():
    """macOS 기본 파이썬이 3.9 다. 이보다 높이 잡으면 brew 설치를 강요하게 된다."""
    from capcut_agent.env_check import MIN_PYTHON

    assert MIN_PYTHON <= (3, 9)


def test_the_version_check_accepts_the_floor(monkeypatch, ws):
    from capcut_agent import env_check

    monkeypatch.setattr(env_check.platform, "python_version_tuple",
                        lambda: ("3", "9", "6"))
    monkeypatch.setattr(env_check.platform, "python_version", lambda: "3.9.6")
    report = env_check.run_checks(ws)
    python_check = next(c for c in report.checks if c.name.startswith("python"))
    assert python_check.ok


def test_the_version_check_rejects_too_old(monkeypatch, ws):
    from capcut_agent import env_check

    monkeypatch.setattr(env_check.platform, "python_version_tuple",
                        lambda: ("3", "7", "0"))
    monkeypatch.setattr(env_check.platform, "python_version", lambda: "3.7.0")
    report = env_check.run_checks(ws)
    python_check = next(c for c in report.checks if c.name.startswith("python"))
    assert not python_check.ok
    assert python_check.fix
