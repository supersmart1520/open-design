"""whisper 자가진단 — 전사가 **실제로** 도는지 끝까지 돌려 본다.

`check` 는 "ffmpeg 이 설치돼 있나" 같은 겉을 본다. 이건 모델을 실제로 올리고
소리를 넣어 단어 타임스탬프까지 받아 본다. 둘은 다른 질문이다.

캐시를 건너뛴다. 캐시가 적중하면 모델이 안 돌아도 성공처럼 보인다.
"""

from __future__ import annotations

import platform
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import storage
from .media import FFMPEG, media_duration
from .transcribe import BackendMissing, default_model, pick_backend, transcribe

#: 진단에 쓸 길이 (초). 길면 오래 걸리고 짧으면 측정이 흔들린다.
DEFAULT_SECONDS = 20


@dataclass
class Finding:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Diagnosis:
    backend: str
    model: str
    source: str
    seconds: float
    elapsed: float = 0.0
    findings: list[Finding] = field(default_factory=list)
    sample: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and all(f.ok for f in self.findings)

    @property
    def speed(self) -> float:
        """오디오 1초를 처리하는 데 걸린 실제 시간의 역수 (배속)."""
        return self.seconds / self.elapsed if self.elapsed > 0 else 0.0


def make_test_clip(destination: Path, seconds: int = 5) -> Path:
    """소리가 있는 짧은 클립을 만든다. 쓸 영상이 하나도 없을 때의 최후 수단.

    합성음에는 말이 없으므로 전사 결과가 비는 게 정상이다. 그래도 모델이
    올라가고 well-formed 한 응답을 주는지는 확인된다.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
         "-c:a", "aac", str(destination)],
        check=True,
    )
    return destination


def trim(source: Path, destination: Path, seconds: int) -> Path:
    """앞부분만 잘라 쓴다. 40분짜리로 진단하면 진단에만 20분이 걸린다."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(source),
         "-t", str(seconds), "-vn", "-c:a", "aac", str(destination)],
        check=True,
    )
    return destination


def pick_source(workspace, explicit: str | Path | None) -> tuple[Path, str]:
    """진단에 쓸 소리. (경로, 설명) 을 돌려준다."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"그런 파일이 없습니다: {path}")
        return path, "지정한 파일"

    for folder, label in ((workspace.inbox, "inbox"), (workspace.done, "done")):
        for candidate in storage.list_inbox(workspace) if folder == workspace.inbox else []:
            return candidate, f"{label} 의 첫 영상"
    return Path(), "합성음"


def run(workspace, explicit=None, seconds: int = DEFAULT_SECONDS,
        model: str = "", language: str = "ko", on_progress=print) -> Diagnosis:
    backend = pick_backend(platform.system(), platform.machine())
    model = model or default_model(backend)
    source, label = pick_source(workspace, explicit)

    scratch = workspace.asr_cache / "_doctor"
    scratch.mkdir(parents=True, exist_ok=True)
    clip = scratch / "probe.m4a"

    origin = str(source) if source.name else "합성음"

    # 소리를 준비하다 실패하면 **진단 결과로** 보고한다. 자가진단이 스택
    # 트레이스를 뱉으면, 무엇이 왜 안 되는지 알려주는 제 일을 못 한다.
    try:
        if source.name:
            on_progress(f"  … {label} 앞 {seconds}초를 잘라냅니다")
            trim(source, clip, seconds)
        else:
            on_progress("  … 쓸 영상이 없어 합성음으로 확인합니다 (말은 없습니다)")
            make_test_clip(clip, min(seconds, 5))
        measured = media_duration(clip)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return Diagnosis(
            backend=backend, model=model, source=origin, seconds=0.0,
            error=f"소리를 준비하지 못했습니다 — {type(exc).__name__}: {exc}",
        )

    diagnosis = Diagnosis(
        backend=backend, model=model, source=origin, seconds=measured
    )

    on_progress(f"  … 모델을 올리고 전사합니다 ({backend} / {model})")
    started = time.monotonic()
    try:
        transcript = transcribe(
            clip, scratch, backend, model=model, language=language,
            on_progress=lambda message: None, force=True,
        )
    except BackendMissing as exc:
        diagnosis.error = str(exc)
        return diagnosis
    except Exception as exc:
        diagnosis.error = f"{type(exc).__name__}: {exc}"
        return diagnosis
    finally:
        diagnosis.elapsed = time.monotonic() - started
        clip.unlink(missing_ok=True)

    synthetic = origin == "합성음"
    diagnosis.findings = [
        Finding("백엔드 임포트", True, backend),
        Finding("모델 로드 + 전사", True, f"{diagnosis.elapsed:.1f}초"),
        Finding(
            "문장 인식",
            synthetic or bool(transcript.segments),
            f"{len(transcript.segments)}문장"
            + ("  (합성음이라 비는 게 정상)" if synthetic else ""),
        ),
        Finding(
            "단어 타임스탬프",
            synthetic or bool(transcript.words),
            f"{len(transcript.words)}어절"
            + ("" if transcript.words or synthetic
               else "  ← 없으면 컷이 단어 경계를 못 맞춥니다"),
        ),
        Finding(
            "언어 판정",
            synthetic or transcript.language in ("", language),
            transcript.language or "(없음)",
        ),
    ]
    diagnosis.sample = [s.text.strip() for s in transcript.segments[:3]]
    return diagnosis


def format_diagnosis(diagnosis: Diagnosis) -> str:
    lines = [
        f"백엔드    : {diagnosis.backend}",
        f"모델      : {diagnosis.model}",
        f"소리      : {diagnosis.source}  ({diagnosis.seconds:.1f}초)",
        "",
    ]
    if diagnosis.error:
        lines += [f"실패: {diagnosis.error}", "",
                  "whisper 가 연결되지 않았습니다. 설치 후 다시 확인하세요."]
        return "\n".join(lines)

    for finding in diagnosis.findings:
        lines.append(
            f"[{'OK  ' if finding.ok else 'MISS'}] {finding.name}"
            + (f"  ({finding.detail})" if finding.detail else "")
        )
    lines.append("")
    if diagnosis.sample:
        lines.append("들은 말:")
        lines += [f"  {text}" for text in diagnosis.sample]
        lines.append("")
    if diagnosis.speed:
        lines.append(f"속도      : 실시간의 {diagnosis.speed:.1f}배")
        lines.append(
            f"            10분 영상 전사에 약 "
            f"{600 / diagnosis.speed / 60:.1f}분 걸립니다"
        )
        lines.append("")
    lines.append("whisper 가 제대로 돕니다." if diagnosis.ok
                 else "위 MISS 항목을 확인하세요.")
    return "\n".join(lines)
