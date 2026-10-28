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


def _text_tracks(draft_root: Path) -> list[dict]:
    return [t for t in _timeline(draft_root)["tracks"] if t["type"] == "text"]


def _speech_track(draft_root: Path) -> dict:
    """말하는 자막 트랙. 제목 트랙(세그먼트 1개)과 구분한다."""
    tracks = _text_tracks(draft_root)
    return max(tracks, key=lambda t: len(t["segments"]))


def test_caption_builds_a_draft_with_a_text_track(stub_probe, ws, draft_root, video):
    # 제목은 앞 3초를 차지하므로, 자막 장수를 세는 검사에서는 끈다.
    assert _run(ws, draft_root, video, ["--no-title"]) == 0
    assert len(_speech_track(draft_root)["segments"]) >= len(LINES)


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


def test_speech_subtitle_position_is_given_in_canvas_pixels(stub_probe, ws, draft_root, video):
    """캡컷 UI 가 보여주는 픽셀을 그대로 받는다. 소스가 1080 세로이므로 절반은 540."""
    _run(ws, draft_root, video, ["--speech-y", "-270"])
    y = _speech_track(draft_root)["segments"][0]["clip"]["transform"]["y"]
    assert y == pytest.approx(-270 / 540)


def test_speech_subtitles_default_to_the_agreed_position(stub_probe, ws, draft_root, video):
    from capcut_agent.draft import SPEECH_Y_PX

    _run(ws, draft_root, video)
    y = _speech_track(draft_root)["segments"][0]["clip"]["transform"]["y"]
    assert y == pytest.approx(SPEECH_Y_PX / 540)


def test_subtitles_are_horizontally_centred(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video)
    assert _speech_track(draft_root)["segments"][0]["clip"]["transform"]["x"] == 0.0


def test_shorter_lines_produce_more_cues(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--max-chars", "8"])
    many = len(_speech_track(draft_root)["segments"])
    for folder in draft_root.iterdir():
        if folder.is_dir():
            for item in folder.iterdir():
                item.unlink()
            folder.rmdir()
    _run(ws, draft_root, video, ["--max-chars", "24"])
    few = len(_speech_track(draft_root)["segments"])
    assert many > few


def test_caption_writes_the_script_and_a_report(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video)
    script = ws.scripts / "강의.대본.txt"
    assert script.is_file()
    assert LINES[0] in script.read_text("utf-8")
    report = json.loads(next(ws.reports.glob("*.json")).read_text("utf-8"))
    assert report["모드"] == "caption"
    assert report["자막"]["장수"] >= 1


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


# ------------------------------------------------- inbox 일괄 처리


def _run_main(ws, draft_root, extra: list[str]) -> int:
    argv = cli.normalize_argv(["caption", *extra])
    args = cli.build_parser().parse_args(argv)
    args.draft_dir = str(draft_root)
    return cli.run_caption(args, ws)


def test_inbox_mode_needs_no_filename(stub_probe, ws, draft_root, video):
    """파일명에 괄호·공백이 있어도 타이핑하지 않아도 되게."""
    assert _run_main(ws, draft_root, ["--inbox"]) == 0
    assert len([p for p in draft_root.iterdir() if p.is_dir()]) == 1


def test_inbox_mode_leaves_the_sources_in_place(stub_probe, ws, draft_root, video):
    _run_main(ws, draft_root, ["--inbox"])
    assert video.is_file()
    assert not any(ws.done.iterdir())


def test_an_empty_inbox_is_not_an_error(stub_probe, ws, draft_root, video):
    video.unlink()
    assert _run_main(ws, draft_root, ["--inbox"]) == 0


def test_neither_a_path_nor_inbox_is_a_usage_error(stub_probe, ws, draft_root):
    assert _run_main(ws, draft_root, []) == 2


# --------------------------------------------------------- 제목 자막


def _title_track(draft_root: Path) -> dict:
    tracks = _text_tracks(draft_root)
    assert len(tracks) == 2, "말하는 자막 트랙과 제목 트랙이 따로 있어야 합니다"
    return min(tracks, key=lambda t: len(t["segments"]))


def test_a_title_is_added_by_default(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video)
    assert len(_title_track(draft_root)["segments"]) == 1


def _title_content(draft_root: Path) -> dict:
    timeline = _timeline(draft_root)
    segment = _title_track(draft_root)["segments"][0]
    material = next(m for m in timeline["materials"]["texts"]
                    if m["id"] == segment["material_id"])
    return json.loads(material["content"])


def test_the_title_is_two_lines(stub_probe, ws, draft_root, video):
    """썸네일 제목은 1행 키워드 + 2행 결론 구조다."""
    _run(ws, draft_root, video, ["--title", "학습코칭 선택한 이유"])
    assert _title_content(draft_root)["text"] == "학습코칭\n선택한 이유"


def test_the_two_lines_get_different_colours(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--title", "학습코칭 선택한 이유",
                                 "--title-palette", "green"])
    styles = _title_content(draft_root)["styles"]
    assert len(styles) == 2
    accent, plain = (st["fill"]["content"]["solid"]["color"] for st in styles)
    assert accent != plain
    assert plain == [1.0, 1.0, 1.0]       # 2행은 흰색


def test_the_red_palette_swaps_the_accent(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--title", "학습코칭 선택한 이유",
                                 "--title-palette", "red"])
    accent = _title_content(draft_root)["styles"][0]["fill"]["content"]["solid"]["color"]
    assert accent[0] > accent[1]          # 빨강이 가장 세다


def test_a_long_second_line_is_rendered_smaller(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--title", "부모님 잔소리 아이한테 하지마세요"])
    styles = _title_content(draft_root)["styles"]
    assert styles[1]["size"] < styles[0]["size"]


def test_two_short_lines_stay_the_same_size(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--title", "학습코칭 선택한 이유"])
    styles = _title_content(draft_root)["styles"]
    assert styles[1]["size"] == styles[0]["size"]


def test_the_title_sits_left_of_centre(stub_probe, ws, draft_root, video):
    """인물은 보통 오른쪽에 있다. 얼굴과 제스처를 가리지 않게 살짝 왼쪽."""
    _run(ws, draft_root, video, ["--title", "학습코칭 선택한 이유"])
    assert _title_track(draft_root)["segments"][0]["clip"]["transform"]["x"] < 0


def test_an_explicit_title_wins_over_the_derived_one(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--title", "내가 정한 제목"])
    assert "내가" in _title_content(draft_root)["text"]


def test_the_title_sits_at_the_agreed_position(stub_probe, ws, draft_root, video):
    from capcut_agent.draft import TITLE_Y_PX

    _run(ws, draft_root, video, ["--title", "제목"])
    y = _title_track(draft_root)["segments"][0]["clip"]["transform"]["y"]
    assert y == pytest.approx(TITLE_Y_PX / 540)


def test_the_title_is_a_short_hook_not_a_permanent_banner(
    stub_probe, ws, draft_root, video
):
    """썸네일 후킹이라 길 필요가 없다. 기본 3초."""
    from capcut_agent.draft import US

    _run(ws, draft_root, video, ["--title", "학습코칭 선택한 이유"])
    span = _title_track(draft_root)["segments"][0]["target_timerange"]
    assert span["start"] == 0
    assert span["duration"] == pytest.approx(3 * US, rel=0.01)


def test_the_title_duration_is_configurable(stub_probe, ws, draft_root, video):
    from capcut_agent.draft import US

    _run(ws, draft_root, video, ["--title", "학습코칭 선택한 이유",
                                 "--title-seconds", "1.5"])
    span = _title_track(draft_root)["segments"][0]["target_timerange"]
    assert span["duration"] == pytest.approx(1.5 * US, rel=0.01)


def test_no_speech_subtitle_overlaps_the_title(stub_probe, ws, draft_root, video):
    """제목이 떠 있는 동안 다른 자막이 겹치면 화면이 무너진다."""
    from capcut_agent.draft import US

    _run(ws, draft_root, video, ["--title", "학습코칭 선택한 이유",
                                 "--title-seconds", "3"])
    title_end = 3 * US
    for segment in _speech_track(draft_root)["segments"]:
        assert segment["target_timerange"]["start"] >= title_end - 1


def test_no_title_leaves_a_single_text_track(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--no-title"])
    assert len(_text_tracks(draft_root)) == 1


def test_the_title_renders_above_the_speech_subtitles(stub_probe, ws, draft_root, video):
    _run(ws, draft_root, video, ["--title", "제목"])
    title = _title_track(draft_root)["segments"][0]["render_index"]
    speech = _speech_track(draft_root)["segments"][0]["render_index"]
    assert title > speech


# ----------------------------------------------------- 컷 + 자막 동시


@pytest.fixture
def stub_silence(monkeypatch):
    """말 3구간 사이에 무음이 있는 영상인 것처럼."""
    monkeypatch.setattr(
        cli, "detect_silence_raw",
        lambda *a, **k: "silence_start: 3.6\nsilence_end: 4.4\n",
    )


def test_cut_by_silence_follows_the_volume_detector(stub_probe, stub_silence, ws, draft_root, video):
    assert _run(ws, draft_root, video, ["--cut", "--cut-by", "silence"]) == 0
    assert len(_timeline(draft_root)["tracks"][0]["segments"]) == 2


def test_cut_by_speech_follows_the_transcript_gaps(
    stub_probe, stub_silence, ws, draft_root, video
):
    """대본의 단어 사이 틈으로 자른다. 세 문장 사이 0.8초 틈이 둘 있다."""
    assert _run(ws, draft_root, video, ["--cut", "--cut-by", "speech"]) == 0
    assert len(_timeline(draft_root)["tracks"][0]["segments"]) == 3


def test_hybrid_is_the_default(stub_probe, stub_silence, ws, draft_root, video):
    """음량으로 자를 자리를 정하고 경계를 단어 경계로 밀어낸다."""
    assert _run(ws, draft_root, video, ["--cut"]) == 0
    assert len(_timeline(draft_root)["tracks"][0]["segments"]) >= 2


def test_cutting_by_speech_never_loses_a_word_from_the_subtitles(
    stub_probe, stub_silence, ws, draft_root, video
):
    """이번 버그의 핵심.

    음량 기준 컷은 경계가 단어 한가운데 떨어질 수 있어, 말이 남아 있는데
    자막만 사라지는 구간이 생겼다. 대본 기준 컷은 컷이 항상 단어 사이라
    모든 말이 자막으로 남아야 한다.
    """
    # 제목이 앞부분 자막을 가리므로 이 검사에서는 끈다.
    _run(ws, draft_root, video, ["--cut", "--max-chars", "40", "--no-title"])
    timeline = _timeline(draft_root)
    texts = {m["id"]: json.loads(m["content"])["text"] for m in timeline["materials"]["texts"]}
    rendered = " ".join(
        texts[s["material_id"]].replace("\n", " ")
        for s in _speech_track(draft_root)["segments"]
    )
    for line in LINES:
        for token in line.split():
            assert token in rendered, f"{token!r} 가 자막에서 사라졌습니다"


def test_subtitles_cover_the_cut_timeline_without_long_holes(
    stub_probe, stub_silence, ws, draft_root, video
):
    """자막 없는 구간이 생기지 않는지. 컷으로 틈을 걷어냈으니 간격이 짧아야 한다."""
    _run(ws, draft_root, video, ["--cut"])
    segments = _speech_track(draft_root)["segments"]
    spans = sorted(
        (s["target_timerange"]["start"], s["target_timerange"]["duration"])
        for s in segments
    )
    for (start, duration), (next_start, _) in zip(spans, spans[1:]):
        hole_us = next_start - (start + duration)
        assert hole_us <= 1_000_000, f"자막 사이에 {hole_us / 1e6:.2f}초 구멍"


def test_without_cut_the_video_stays_whole(stub_probe, stub_silence, ws, draft_root, video):
    _run(ws, draft_root, video)
    assert len(_timeline(draft_root)["tracks"][0]["segments"]) == 1


def test_cut_shortens_the_timeline(stub_probe, stub_silence, ws, draft_root, video):
    _run(ws, draft_root, video, ["--cut"])
    with_cut = _timeline(draft_root)["duration"]
    for folder in [p for p in draft_root.iterdir() if p.is_dir()]:
        for item in folder.rglob("*"):
            if item.is_file():
                item.unlink()
        for item in sorted(folder.rglob("*"), reverse=True):
            if item.is_dir():
                item.rmdir()
        folder.rmdir()
    _run(ws, draft_root, video)
    assert with_cut < _timeline(draft_root)["duration"]


def test_subtitles_never_outlast_a_cut_timeline(stub_probe, stub_silence, ws, draft_root, video):
    """잘라낸 만큼 자막도 앞으로 당겨져야 한다. 안 그러면 끝에서 어긋난다."""
    _run(ws, draft_root, video, ["--cut"])
    timeline = _timeline(draft_root)
    for track in _text_tracks(draft_root):
        for segment in track["segments"]:
            span = segment["target_timerange"]
            assert span["start"] + span["duration"] <= timeline["duration"] + 1
