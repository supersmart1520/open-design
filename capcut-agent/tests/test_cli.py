"""CLI 오케스트레이션 — 특히 "드래프트가 가리키는 미디어가 실제로 거기 있는가".

실제 사용에서 터진 버그: 드래프트를 쓴 **뒤에** 원본을 done/ 으로 옮기면
드래프트 안의 절대경로가 빈 자리를 가리켜, 캡컷이 열 때 미디어 재연결을
요구한다. 아래 테스트가 그 순서를 못박는다. ffmpeg 없이 돌도록 probe 와
silencedetect 는 가짜로 바꿔 끼운다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capcut_agent import cli, storage
from capcut_agent.media import MediaInfo

#: 말 3구간(0–1.5 / 2.4–5.0 / 6.2–10.0) + 꼬리 무음이 있는 12초 클립 흉내.
FAKE_SILENCE = (
    "silence_start: 1.5\nsilence_end: 2.4\n"
    "silence_start: 5.0\nsilence_end: 6.2\n"
    "silence_start: 10.0\n"
)


@pytest.fixture
def stub_ffmpeg(monkeypatch):
    def fake_probe(video):
        return MediaInfo(
            path=Path(video).expanduser().resolve(),
            width=1920, height=1080, fps=30.0, duration=12.0, has_audio=True,
        )

    monkeypatch.setattr(cli, "probe", fake_probe)
    monkeypatch.setattr(cli, "detect_silence_raw", lambda *a, **k: FAKE_SILENCE)


@pytest.fixture
def ws(tmp_path: Path) -> storage.Workspace:
    return storage.resolve_workspace(tmp_path / "CapCutAgent")


@pytest.fixture
def draft_root(tmp_path: Path) -> Path:
    root = tmp_path / "drafts"
    root.mkdir()
    return root


def _run(ws, draft_root, video: Path, extra: list[str] | None = None) -> int:
    # main() 과 같은 경로로 파싱한다 — 하위호환 정규화를 포함해서.
    argv = cli.normalize_argv([str(video), *(extra or [])])
    args = cli.build_parser().parse_args(argv)
    options = cli.resolve_options(ws, args)
    return cli.process_one(video, ws, options, args, draft_root)


def _drafted_media_path(draft_root: Path) -> Path:
    folders = [p for p in draft_root.iterdir() if p.is_dir()]
    assert len(folders) == 1, f"드래프트 폴더가 1개여야 합니다: {folders}"
    timeline = json.loads((folders[0] / "draft_content.json").read_text("utf-8"))
    return Path(timeline["materials"]["videos"][0]["path"])


def _inbox_clip(ws: storage.Workspace, name: str = "talk.mp4") -> Path:
    clip = ws.inbox / name
    clip.write_bytes(b"fake-mp4-bytes")
    return clip


def test_draft_points_at_a_file_that_actually_exists(stub_ffmpeg, ws, draft_root):
    """회귀 테스트. 이게 깨지면 캡컷이 미디어 재연결을 요구한다."""
    clip = _inbox_clip(ws)
    assert _run(ws, draft_root, clip) == 0
    drafted = _drafted_media_path(draft_root)
    assert drafted.is_file(), f"드래프트가 없는 파일을 가리킵니다: {drafted}"


def test_draft_points_at_the_archived_copy_not_the_inbox(stub_ffmpeg, ws, draft_root):
    clip = _inbox_clip(ws)
    _run(ws, draft_root, clip)
    assert _drafted_media_path(draft_root) == (ws.done / "talk.mp4").resolve()
    assert not clip.exists()


def test_keep_source_points_the_draft_at_the_inbox_file(stub_ffmpeg, ws, draft_root):
    clip = _inbox_clip(ws)
    _run(ws, draft_root, clip, ["--keep-source"])
    drafted = _drafted_media_path(draft_root)
    assert drafted == clip.resolve()
    assert drafted.is_file()
    assert not any(ws.done.iterdir())


def test_a_file_outside_the_inbox_is_never_moved(stub_ffmpeg, ws, draft_root, tmp_path):
    outside = tmp_path / "elsewhere" / "talk.mp4"
    outside.parent.mkdir()
    outside.write_bytes(b"fake-mp4-bytes")
    _run(ws, draft_root, outside)
    assert _drafted_media_path(draft_root) == outside.resolve()
    assert outside.is_file()


def test_report_records_both_the_inbox_and_final_paths(stub_ffmpeg, ws, draft_root):
    clip = _inbox_clip(ws)
    _run(ws, draft_root, clip)
    reports = list(ws.reports.glob("*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text("utf-8"))
    assert payload["원본"]["경로"] == str((ws.done / "talk.mp4").resolve())
    assert payload["원본"]["투입경로"] == str(clip.resolve())


def test_report_omits_the_inbox_path_when_nothing_moved(stub_ffmpeg, ws, draft_root):
    _run(ws, draft_root, _inbox_clip(ws), ["--keep-source"])
    payload = json.loads(next(ws.reports.glob("*.json")).read_text("utf-8"))
    assert payload["원본"]["투입경로"] is None


def test_dry_run_writes_nothing_and_moves_nothing(stub_ffmpeg, ws, draft_root):
    clip = _inbox_clip(ws)
    assert _run(ws, draft_root, clip, ["--dry-run"]) == 0
    assert clip.is_file()
    assert not any(draft_root.iterdir())
    assert not any(ws.reports.iterdir())


def test_a_clip_without_audio_lands_in_failed(stub_ffmpeg, ws, draft_root, monkeypatch):
    monkeypatch.setattr(
        cli, "probe",
        lambda video: MediaInfo(
            path=Path(video).resolve(), width=1920, height=1080,
            fps=30.0, duration=12.0, has_audio=False,
        ),
    )
    clip = _inbox_clip(ws)
    assert _run(ws, draft_root, clip) == 1
    assert (ws.failed / "talk.mp4").is_file()
    assert not any(draft_root.iterdir())


def test_cut_plan_survives_the_round_trip(stub_ffmpeg, ws, draft_root):
    """가짜 silencedetect 가 기대한 컷으로 이어지는지 — 계획 단계 회귀 방지."""
    _run(ws, draft_root, _inbox_clip(ws))
    payload = json.loads(next(ws.reports.glob("*.json")).read_text("utf-8"))
    assert payload["결과"]["보존조각"] == 3
    assert payload["결과"]["컷수"] == 2
    # 꼬리 무음 2초 + 중간 무음 2.1초에서 패딩만큼 돌려받는다.
    assert 8.0 < payload["결과"]["출력길이초"] < 8.6


# --------------------------------------------------- 하위호환 인자 정규화


def test_a_bare_video_path_is_treated_as_cut():
    assert cli.normalize_argv(["영상.mp4"]) == ["cut", "영상.mp4"]


def test_bare_flags_are_treated_as_cut():
    assert cli.normalize_argv(["--inbox"]) == ["cut", "--inbox"]
    assert cli.normalize_argv(["영상.mp4", "--dry-run"]) == ["cut", "영상.mp4", "--dry-run"]


def test_the_old_check_flag_maps_to_the_check_command():
    assert cli.normalize_argv(["--check"]) == ["check"]


def test_explicit_subcommands_pass_through_untouched():
    for command in cli.SUBCOMMANDS:
        assert cli.normalize_argv([command, "x"]) == [command, "x"]


def test_help_passes_through():
    assert cli.normalize_argv(["--help"]) == ["--help"]


def test_a_leading_workspace_flag_is_kept_before_the_command():
    assert cli.normalize_argv(["--workspace", "/tmp/ws", "영상.mp4"]) == [
        "--workspace", "/tmp/ws", "cut", "영상.mp4"
    ]


def test_empty_argv_stays_empty():
    assert cli.normalize_argv([]) == []


# ------------------------------------------------- Claude Code 스킬 설치


def test_the_skill_file_ships_with_the_package():
    skill = Path(cli.__file__).resolve().parent.parent / "SKILL.md"
    assert skill.is_file()
    body = skill.read_text("utf-8")
    assert body.startswith("---")
    assert "name: capcut-agent" in body


def test_the_skill_states_that_building_is_not_verifying():
    """사람이 캡컷에서 재생해야 끝난다 — 스킬이 이걸 빠뜨리면 안 된다."""
    skill = Path(cli.__file__).resolve().parent.parent / "SKILL.md"
    body = skill.read_text("utf-8")
    assert "빌드 성공은 검증이 아니다" in body


def test_installing_the_skill_links_it(tmp_path):
    args = cli.build_parser().parse_args(
        ["install-skill", "--dest", str(tmp_path / "capcut-agent")]
    )
    assert cli.run_install_skill(args) == 0
    installed = tmp_path / "capcut-agent" / "SKILL.md"
    assert installed.is_file()
    assert "capcut-agent" in installed.read_text("utf-8")


def test_installing_twice_replaces_rather_than_failing(tmp_path):
    dest = str(tmp_path / "capcut-agent")
    for _ in range(2):
        args = cli.build_parser().parse_args(["install-skill", "--dest", dest])
        assert cli.run_install_skill(args) == 0


def test_copy_mode_survives_the_source_moving(tmp_path):
    dest = tmp_path / "capcut-agent"
    args = cli.build_parser().parse_args(
        ["install-skill", "--dest", str(dest), "--copy"]
    )
    cli.run_install_skill(args)
    installed = dest / "SKILL.md"
    assert not installed.is_symlink()
    assert installed.read_text("utf-8").startswith("---")


# --- 어느 아키텍처로 띄울지 ---------------------------------------------------
#
# universal2 파이썬은 arm64 로도 x86_64 로도 뜨는데, 확장 모듈은 pip 를 돌린
# 쪽 하나만 깔려 있다. 터미널에서 되는 것이 `.app` 안에서 된다는 보장이 없다.


def test_working_arch_picks_the_one_that_imports(monkeypatch):
    import subprocess as sp

    from capcut_agent.cli import working_arch

    tried = []

    def fake_run(command, **kwargs):
        tried.append(command[:2])
        # arm64 만 실패하는 기계.
        failed = command[:2] == ["/usr/bin/arch", "-arm64"]
        return sp.CompletedProcess(command, 1 if failed else 0, b"", b"")

    monkeypatch.setattr(sp, "run", fake_run)

    assert working_arch("/py") == ("/usr/bin/arch", "-x86_64")
    assert tried[0] == ["/usr/bin/arch", "-arm64"]


def test_working_arch_returns_none_when_nothing_imports(monkeypatch):
    import subprocess as sp

    from capcut_agent.cli import working_arch

    monkeypatch.setattr(
        sp, "run",
        lambda command, **kw: sp.CompletedProcess(command, 1, b"", b""),
    )
    assert working_arch("/py") is None


def test_working_arch_prefers_plain_when_arch_is_absent(monkeypatch):
    # 인텔 맥이나 arch 가 없는 환경. 그냥 실행이 통하면 그걸 쓴다.
    import subprocess as sp

    from capcut_agent.cli import working_arch

    def fake_run(command, **kwargs):
        if command[0] == "/usr/bin/arch":
            raise FileNotFoundError("arch 가 없습니다")
        return sp.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(sp, "run", fake_run)

    assert working_arch("/py") == ()


def test_mac_launcher_hunts_for_an_importable_arch():
    from pathlib import Path

    from capcut_agent.cli import _mac_launcher

    script = _mac_launcher(Path("/pkg"), "/py")

    # 인터프리터가 뜨는지만 보면 안 된다. import 가 되는지를 봐야 한다.
    assert "import fastapi, uvicorn" in script
    assert '"/usr/bin/arch -arm64" "/usr/bin/arch -x86_64" ""' in script
    # 어느 쪽도 안 되면 조용히 끝내지 않는다.
    assert "필요한 패키지를 읽지 못했습니다" in script


def test_window_note_names_the_actual_python():
    # venv 로 깔면 `python3` 은 그 venv 가 아니다. 복사해 붙인 명령이
    # 엉뚱한 파이썬에 설치하면, 창은 여전히 안 뜨고 이유도 모르게 된다.
    from capcut_agent.cli import window_note

    venv = "/Users/x/CapCutAgent/venv/bin/python"
    assert venv in window_note(venv)
    assert "python3 -m pip" not in window_note(venv)
