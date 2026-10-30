"""작업 워커 — 큐에 쌓인 영상을 하나씩 편집한다.

파이프라인을 다시 구현하지 않고 **CLI 를 그대로 부른다.** 이미 테스트된
경로이고, 대시보드와 터미널이 서로 다르게 동작할 일이 없다. 결과는 CLI 가
`__CAPCUT_JSON__` 줄로 내보내는 구조화된 보고서로 받는다.

한 번에 하나만 돌린다. ASR 은 스레드 안전하지 않아 어차피 직렬화해야 한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from .events import EventBroker
from .jobs import JobStore

#: CLI 가 구조화된 결과를 실어 보내는 줄의 접두사.
RESULT_PREFIX = "__CAPCUT_JSON__ "

#: 단계 사이 최소 간격 (초).
#: 전사가 캐시에서 바로 나오면 단계가 깜빡이고 끝나 무슨 일이 있었는지 안 보인다.
MIN_STAGE_SECONDS = 0.5

#: 큐가 비었을 때 쉬는 간격 (초).
IDLE_SLEEP = 0.4


def build_command(job, workspace: Path, draft_dir: str | None = None) -> list[str]:
    """작업 옵션을 CLI 인자로. 대시보드가 쓰는 유일한 조립 지점."""
    options = job.options or {}
    base = [
        sys.executable, "-m", "capcut_agent.cli",
        "--workspace", str(workspace),
    ]
    count = int(options.get("shortsCount") or 0)

    if count >= 1:
        # 한 영상에서 숏폼 여러 개. 점수 상위 N개를 바로 만든다.
        command = base + ["shorts", job.source, "--json", "--auto", str(count)]
        pairs = (
            ("--motion", "motion"), ("--font-size", "fontSize"),
            ("--subtitle-y", "speechY"), ("--anchor", "anchor"),
            ("--title-layout", "titleLayout"),
            ("--title-palette", "titlePalette"),
            ("--language", "language"),
        )
        if options.get("srt", True):
            command.append("--srt")
        if options.get("noTitle"):
            # 제목을 끄랬더니 자막을 끄고 있었다. 뜻이 전혀 다르다.
            command.append("--no-title")
    else:
        command = base + ["caption", job.source, "--json"]
        if options.get("cut", True):
            command.append("--cut")
        if options.get("shorts"):
            command.append("--shorts")
        if options.get("srt", True):
            command.append("--srt")
        if options.get("noTitle"):
            command.append("--no-title")
        pairs = (
            ("--motion", "motion"), ("--font-size", "fontSize"),
            ("--speech-y", "speechY"), ("--title-y", "titleY"),
            ("--title", "title"), ("--title-layout", "titleLayout"),
            ("--title-palette", "titlePalette"), ("--anchor", "anchor"),
            ("--cut-by", "cutBy"), ("--media-mode", "mediaMode"),
            ("--language", "language"),
        )

    for flag, key in pairs:
        value = options.get(key)
        if value not in (None, ""):
            command += [flag, str(value)]
    if draft_dir:
        command += ["--draft-dir", draft_dir]
    return command


def parse_result(lines: list[str]) -> dict | None:
    """CLI 출력에서 구조화된 보고서를 꺼낸다."""
    for line in reversed(lines):
        if line.startswith(RESULT_PREFIX):
            try:
                return json.loads(line[len(RESULT_PREFIX):])
            except json.JSONDecodeError:
                return None
    return None


def summarize(report: dict) -> dict:
    """대시보드 카드에 올릴 요약. 한글 키를 영문으로 눕힌다."""
    source = report.get("원본", {})
    subtitle = report.get("자막", {})
    cut = report.get("컷", {})
    return {
        "draft": report.get("드래프트"),
        "script": report.get("대본파일"),
        "post": report.get("게시문구"),
        "canvas": report.get("캔버스"),
        "durationSec": source.get("길이초"),
        "resolution": source.get("해상도"),
        "rotation": source.get("회전"),
        "cues": subtitle.get("장수"),
        "title": subtitle.get("제목"),
        "maxChars": subtitle.get("한줄글자수"),
        "motion": subtitle.get("모션"),
        "cutApplied": cut.get("적용"),
        "cutPieces": cut.get("보존조각"),
        "outputSec": cut.get("출력길이초"),
        "mediaMode": report.get("미디어반입"),
    }


class Worker:
    """큐를 비우는 단일 스레드."""

    def __init__(
        self,
        store: JobStore,
        broker: EventBroker,
        workspace: Path,
        draft_dir: str | None = None,
    ):
        self.store = store
        self.broker = broker
        self.workspace = Path(workspace)
        self.draft_dir = draft_dir
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ 수명주기

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="capcut-worker",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------- 루프

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.store.next_queued()
            if job is None:
                self._stop.wait(IDLE_SLEEP)
                continue
            try:
                self.run_job(job.id)
            except Exception as exc:  # 워커 스레드는 절대 죽으면 안 된다.
                self._fail(job.id, f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------ 한 작업

    def run_job(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        if not Path(job.source).is_file():
            self._fail(job_id, f"원본 파일이 없습니다: {job.source}")
            return

        started_at = time.monotonic()
        self._emit(self.store.transition(job_id, "running", message="시작"))
        command = build_command(job, self.workspace, self.draft_dir)
        lines: list[str] = []
        last_stage = 0.0

        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            stage = _stage_of(line)
            if stage is None:
                continue
            # 캐시 적중으로 단계가 순식간에 지나가면 무슨 일이 있었는지 안 보인다.
            elapsed = time.monotonic() - last_stage
            if last_stage and elapsed < MIN_STAGE_SECONDS:
                time.sleep(MIN_STAGE_SECONDS - elapsed)
            last_stage = time.monotonic()
            self._emit(self.store.progress(job_id, stage, line.strip()))

        code = process.wait()
        report = parse_result(lines)
        if code != 0 or report is None:
            tail = "\n".join(line for line in lines[-6:] if line.strip())
            self._fail(job_id, tail or f"CLI 가 {code} 로 끝났습니다")
            return

        self._emit(self.store.transition(
            job_id, "review", stage="done",
            message="캡컷에서 확인해 주세요",
            result={
                "report": report,
                "summary": summarize(report),
                "elapsedSec": round(time.monotonic() - started_at, 1),
            },
        ))

    # ------------------------------------------------------------- 보조

    def _fail(self, job_id: str, error: str) -> None:
        try:
            self._emit(self.store.transition(
                job_id, "failed", stage="failed", message="실패", error=error
            ))
        except Exception:
            pass

    def _emit(self, job) -> None:
        if job is not None:
            self.broker.publish("job", {"job": job.to_dict()})


#: CLI 출력 줄 → 단계 이름.
_STAGE_MARKERS = (
    ("오디오 추출", "extract"),
    ("전사", "asr"),
    ("컷", "cut"),
    ("자막", "subtitle"),
    ("문장", "subtitle"),
    ("드래프트", "draft"),
    ("미디어", "media"),
)


def _stage_of(line: str) -> str | None:
    text = line.strip()
    if not text:
        return None
    for marker, stage in _STAGE_MARKERS:
        if text.startswith("…") or text.startswith("… "):
            if marker in text:
                return stage
        elif text.startswith(marker):
            return stage
    return None
