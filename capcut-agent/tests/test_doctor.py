"""whisper 자가진단 — 실패를 **보고**하는지 본다.

진단 도구가 스택 트레이스를 뱉으면, 무엇이 왜 안 되는지 알려주는 제 일을
못 한다. 전사 자체는 모델이 필요해서 여기서 돌리지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from capcut_agent import doctor, storage


def _workspace(tmp_path: Path):
    return storage.resolve_workspace(tmp_path / "ws")


def test_broken_audio_is_reported_not_raised(tmp_path, monkeypatch):
    # 소리를 준비하다 깨지는 경우. 예전에는 여기서 터져 나갔다.
    workspace = _workspace(tmp_path)
    monkeypatch.setattr(
        doctor, "make_test_clip",
        lambda destination, seconds=5: destination.write_bytes(b"not audio"),
    )

    result = doctor.run(workspace, on_progress=lambda message: None)

    assert not result.ok
    assert "소리를 준비하지" in result.error
    assert result.seconds == 0.0


def test_real_audio_clip_gets_measured(tmp_path, monkeypatch):
    """소리만 있는 클립의 길이를 읽어낸다.

    예전에는 `probe()` 로 조사해서 "비디오 트랙이 없습니다" 로 거부당했고,
    자가진단이 전사에 닿지도 못하고 죽었다. 가짜 파일로는 이걸 못 잡는다 —
    깨진 파일은 어느 쪽으로도 실패하므로, **멀쩡한 오디오**여야 한다.
    """
    workspace = _workspace(tmp_path)

    # 모델은 올리지 않는다. 길이를 재는 데까지만 보면 된다.
    def no_model(*args, **kwargs):
        raise doctor.BackendMissing("여기서는 모델을 올리지 않습니다")

    monkeypatch.setattr(doctor, "transcribe", no_model)

    result = doctor.run(workspace, seconds=3, on_progress=lambda message: None)

    assert result.seconds > 0, f"길이를 못 읽었습니다: {result.error}"
    assert "비디오 트랙이 없습니다" not in result.error
    assert result.source == "합성음"


def test_trim_failure_names_the_cause(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    video = workspace.inbox / "강의.mp4"
    video.write_bytes(b"fake")

    def explode(source, destination, seconds):
        raise RuntimeError("ffmpeg 이 없습니다")

    monkeypatch.setattr(doctor, "trim", explode)

    result = doctor.run(workspace, on_progress=lambda message: None)

    assert not result.ok
    assert "ffmpeg 이 없습니다" in result.error
    assert result.source.endswith("강의.mp4")
