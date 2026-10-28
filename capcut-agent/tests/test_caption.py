"""caption — 컷 없이 자막만.

전사 캐시를 미리 심어 whisper 없이 전 구간을 돌린다. 캐시 키가 파일 내용
해시라는 점까지 같이 검증된다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capcut_agent import cli, storage
from capcut_agent.subtitle import Word
from capcut_agent.transcribe import Segment, Transcript, cache_key, default_model


LINES = [
    "오늘은 세 가지를 말씀드리겠습니다.",
    "이걸 알면 결과가 달라집니다.",
    "그래서 순서가 가장 중요합니다.",
]


def _stub_transcript() -> Transcript:
    segments, words, cursor = [], [], 0.0
    for line in LINES:
        span = max(len(line) / 5.0, 2.0)
        segments.append(Segment(line, cursor, cursor + span))
        tokens = line.split()
        step = span / len(tokens)
        for i, token in enumerate(tokens):
            words.append(Word(token, cursor + i * step, cursor + (i + 1) * step - 0.01))
        cursor += span + 0.8
    return Transcript(
        language="ko", text=" ".join(LINES), words=words,
        segments=segments, model=default_model("faster-whisper"),
    )


@pytest.fixture
def ws(tmp_path: Path) -> storage.Workspace:
    return storage.resolve_workspace(tmp_path / "CapCutAgent")


@pytest.fixture
def draft_root(tmp_path: Path) -> Path:
    root = tmp_path / "drafts"
    root.mkdir()
    return root


@pytest.fixture
def video(ws: storage.Workspace) -> Path:
    clip = ws.inbox / "강의.mp4"
    clip.write_bytes(b"pretend-this-is-an-mp4")
    return clip


@pytest.fixture
def seeded(ws: storage.Workspace, video: Path) -> Transcript:
    """ASR 캐시를 심는다. transcribe() 가 whisper 를 부르지 않고 꺼내 쓴다."""
    transcript = _stub_transcript()
    model = default_model("faster-whisper")
    key = cache_key(video, model, "ko")
    ws.asr_cache.mkdir(parents=True, exist_ok=True)
    (ws.asr_cache / f"{key}.json").write_text(
        json.dumps(transcript.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    return transcript


@pytest.fixture
def stub_probe(monkeypatch, seeded):
    from capcut_agent.media import MediaInfo

    def fake_probe(path):
        return MediaInfo(
            path=Path(path).expanduser().resolve(), width=1920, height=1080,
            fps=30.0, duration=seeded.duration + 1.0, has_audio=True,
        )

    monkeypatch.setattr(cli, "probe", fake_probe)
    # 리눅스 컨테이너에서도 faster-whisper 분기를 타게 고정한다.
    monkeypatch.setattr(cli, "pick_backend", lambda *a: "faster-whisper")


def _run(ws, draft_root, video: Path, extra: list[str] | None = None) -> int:
    argv = cli.normalize_argv(["caption", str(video), *(extra or [])])
    args = cli.build_parser().parse_args(argv)
    args.draft_dir = str(draft_root)
    return cli.run_caption(args, ws)


def _timeline(draft_root: Path) -> dict:
    folder = next(p for p in draft_root.iterdir() if p.is_dir())
    return json.loads((folder / "draft_content.json").read_text("utf-8"))


def test_caption_builds_a_draft_with_a_text_track(stub_probe, ws, draft_root, video):
    assert _run(ws, draft_root, video) == 0
    tracks = {t["type"]: t for t in _timeline(draft_root)["tracks"]}
    assert "text" in tracks
    assert len(tracks["text"]["segments"]) >= len(LINES)


def test_caption_keeps_the_whole_video_as_one_clip(stub_probe, ws, draft_root, video):
    """자막만 달라고 했으면 컷은 하나도 없어야 한다."""
    _run(ws, draft_root, video)
    segments = _timeline(draft_root)["tracks"][0]["segments"]
    assert len(segments) == 1
    assert segments[0]["source_timerange"]["start"] == 0


def test_caption_never_moves_the_source(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video)
    assert video.is_file()
    assert not any(ws.done.iterdir())


def test_caption_draft_points_at_an_existing_file(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video)
    path = Path(_timeline(draft_root)["materials"]["videos"][0]["path"])
    assert path.is_file()


def test_caption_keeps_the_original_aspect_by_default(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video)
    config = _timeline(draft_root)["canvas_config"]
    assert (config["width"], config["height"]) == (1920, 1080)
    assert config["ratio"] == "original"


def test_shorts_flag_switches_to_a_vertical_canvas(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--shorts"])
    timeline = _timeline(draft_root)
    assert (timeline["canvas_config"]["width"], timeline["canvas_config"]["height"]) == (1080, 1920)
    assert timeline["materials"]["videos"][0]["crop_ratio"] == "9:16"


def test_subtitle_position_is_honoured(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--subtitle-y", "-0.5"])
    text_track = [t for t in _timeline(draft_root)["tracks"] if t["type"] == "text"][0]
    assert text_track["segments"][0]["clip"]["transform"]["y"] == pytest.approx(-0.5)


def test_shorter_lines_produce_more_cues(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--max-chars", "8"])
    many = len([t for t in _timeline(draft_root)["tracks"] if t["type"] == "text"][0]["segments"])
    for folder in draft_root.iterdir():
        if folder.is_dir():
            for item in folder.iterdir():
                item.unlink()
            folder.rmdir()
    _run(ws, draft_root, video, ["--max-chars", "24"])
    few = len([t for t in _timeline(draft_root)["tracks"] if t["type"] == "text"][0]["segments"])
    assert many > few


def test_caption_writes_the_script_and_a_report(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video)
    script = ws.scripts / "강의.대본.txt"
    assert script.is_file()
    assert LINES[0] in script.read_text("utf-8")
    report = json.loads(next(ws.reports.glob("*.json")).read_text("utf-8"))
    assert report["모드"] == "caption"
    assert report["자막"]["장수"] >= len(LINES)


def test_srt_flag_writes_a_sidecar(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--srt"])
    srt = list(ws.scripts.glob("*.srt"))
    assert len(srt) == 1
    assert "-->" in srt[0].read_text("utf-8")


def test_a_clip_without_audio_is_refused(stub_probe, ws, draft_root, video, monkeypatch):
    from capcut_agent.media import MediaInfo

    monkeypatch.setattr(
        cli, "probe",
        lambda path: MediaInfo(
            path=Path(path).resolve(), width=1920, height=1080,
            fps=30.0, duration=10.0, has_audio=False,
        ),
    )
    assert _run(ws, draft_root, video) == 1
    assert not any(draft_root.iterdir())


def test_the_asr_cache_is_keyed_by_content_not_mtime(ws, video):
    """같은 내용을 다른 이름으로 복사해도 같은 키가 나와야 재사용된다."""
    twin = ws.inbox / "강의-복사.mp4"
    twin.write_bytes(video.read_bytes())
    assert cache_key(video, "m", "ko") == cache_key(twin, "m", "ko")


def test_different_content_gets_a_different_key(ws, video):
    other = ws.inbox / "다른영상.mp4"
    other.write_bytes(b"different-bytes-entirely")
    assert cache_key(video, "m", "ko") != cache_key(other, "m", "ko")


# ------------------------------------------------- 점검 게이트


def _report_with_missing_draft_folder():
    from capcut_agent.env_check import Check, Report

    report = Report(system="Linux", machine="x86_64", track="fallback",
                    track_label="테스트", asr_backend="faster-whisper")
    report.checks = [
        Check("python >= 3.10", True),
        Check(cli.DRAFT_CHECK_NAME, False, fix="캡컷을 설치하세요"),
    ]
    return report


class _Args:
    def __init__(self, draft_dir=None):
        self.draft_dir = draft_dir


def test_an_explicit_draft_dir_bypasses_the_discovery_check():
    report = _report_with_missing_draft_folder()
    assert cli.blocking_checks(report, _Args(draft_dir="/tmp/drafts")) == []


def test_a_missing_draft_folder_blocks_when_not_given_explicitly():
    report = _report_with_missing_draft_folder()
    blockers = cli.blocking_checks(report, _Args())
    assert [c.name for c in blockers] == [cli.DRAFT_CHECK_NAME]


def test_commands_that_write_no_draft_ignore_the_folder_check():
    report = _report_with_missing_draft_folder()
    assert cli.blocking_checks(report, _Args(), needs_draft=False) == []


def test_other_blockers_are_never_skipped():
    from capcut_agent.env_check import Check

    report = _report_with_missing_draft_folder()
    report.checks.append(Check("ffmpeg", False, fix="brew install ffmpeg"))
    blockers = cli.blocking_checks(report, _Args(draft_dir="/tmp/drafts"))
    assert [c.name for c in blockers] == ["ffmpeg"]
