"""음성 전사 — 로컬 whisper. 대본이 먼저, 편집 판단은 그 다음.

백엔드는 트랙에 따라 갈린다.
    Darwin arm64  → mlx-whisper   (Apple Silicon 가속)
    그 외         → faster-whisper

지뢰
  * **캐시 키는 파일 내용 해시.** mtime 기준으로 잡으면 같은 영상을 다시
    넣을 때마다 miss 가 나서 전사를 처음부터 다시 돌린다.
  * **ASR 호출은 직렬화해야 한다.** faster-whisper 가 쓰는 numba 는 스레드
    안전하지 않아, 동시에 부르면 segfault 로 프로세스가 죽는다.
    여기서는 모듈 수준 락으로 묶는다. 2단의 FastAPI 에서는 asyncio.Lock 으로
    같은 일을 한다.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .media import extract_wav
from .subtitle import Word

#: numba 가 스레드 안전하지 않다. ASR 은 한 번에 하나만.
_ASR_LOCK = threading.Lock()

#: 내용 해시를 읽을 때의 청크 크기.
_HASH_CHUNK = 1024 * 1024


@dataclass
class Segment:
    """whisper 가 끊어준 한 토막. 하이라이트 선별의 기본 단위."""

    text: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Transcript:
    language: str
    text: str
    words: list[Word] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    model: str = ""

    @property
    def duration(self) -> float:
        if self.segments:
            return self.segments[-1].end
        if self.words:
            return self.words[-1].end
        return 0.0

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "model": self.model,
            "text": self.text,
            "segments": [asdict(s) for s in self.segments],
            "words": [asdict(w) for w in self.words],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Transcript:
        return cls(
            language=raw.get("language", ""),
            text=raw.get("text", ""),
            model=raw.get("model", ""),
            segments=[
                Segment(s["text"], float(s["start"]), float(s["end"]))
                for s in raw.get("segments", [])
            ],
            words=[
                Word(w["text"], float(w["start"]), float(w["end"]))
                for w in raw.get("words", [])
            ],
        )

    def script(self) -> str:
        """사람이 읽는 대본. 타임코드 + 문장."""
        lines = []
        for segment in self.segments:
            stamp = f"[{_mmss(segment.start)} → {_mmss(segment.end)}]"
            lines.append(f"{stamp} {segment.text.strip()}")
        return "\n".join(lines)


def _mmss(seconds: float) -> str:
    minutes, secs = divmod(max(seconds, 0.0), 60)
    return f"{int(minutes):02d}:{secs:05.2f}"


def content_hash(path: Path) -> str:
    """파일 내용의 sha256 앞 16자. 캐시 키의 바탕.

    mtime 이 아니라 내용을 보는 이유: 같은 영상을 다시 업로드하거나 복사해
    넣어도 같은 키가 나와야 전사를 재사용할 수 있다.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def cache_key(path: Path, model: str, language: str) -> str:
    return f"{content_hash(path)}-{model.replace('/', '_')}-{language or 'auto'}"


def pick_backend(system: str, machine: str) -> str:
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "mlx-whisper"
    return "faster-whisper"


def default_model(backend: str) -> str:
    if backend == "mlx-whisper":
        return "mlx-community/whisper-large-v3-turbo"
    return "large-v3"


class BackendMissing(RuntimeError):
    """백엔드 패키지가 안 깔려 있을 때. 설치 명령을 담아 올린다."""


def _transcribe_mlx(wav: Path, model: str, language: str) -> dict:
    try:
        import mlx_whisper  # type: ignore[import-not-found]
    except ImportError as exc:
        raise BackendMissing(
            "mlx-whisper 가 없습니다.  pip install mlx-whisper"
        ) from exc
    return mlx_whisper.transcribe(
        str(wav),
        path_or_hf_repo=model,
        language=language or None,
        word_timestamps=True,
    )


def _transcribe_faster(wav: Path, model: str, language: str) -> dict:
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:
        raise BackendMissing(
            "faster-whisper 가 없습니다.  pip install faster-whisper"
        ) from exc
    engine = WhisperModel(model, device="auto", compute_type="auto")
    segments, info = engine.transcribe(
        str(wav), language=language or None, word_timestamps=True
    )
    collected = []
    for segment in segments:
        collected.append({
            "text": segment.text,
            "start": segment.start,
            "end": segment.end,
            "words": [
                {"word": w.word, "start": w.start, "end": w.end}
                for w in (segment.words or [])
            ],
        })
    return {"language": info.language, "segments": collected}


def normalize(raw: dict, model: str, language: str) -> Transcript:
    """두 백엔드의 출력 모양을 하나로 맞춘다.

    mlx-whisper 와 faster-whisper 모두 segments[].words[] 를 주지만 키 이름이
    조금 다르고, 단어 타임스탬프가 비어 올 수도 있다.
    """
    segments: list[Segment] = []
    words: list[Word] = []
    for raw_segment in raw.get("segments", []):
        text = str(raw_segment.get("text", "")).strip()
        start = float(raw_segment.get("start", 0.0))
        end = float(raw_segment.get("end", start))
        if text and end > start:
            segments.append(Segment(text, start, end))
        for raw_word in raw_segment.get("words", []) or []:
            token = str(raw_word.get("word") or raw_word.get("text") or "").strip()
            w_start = raw_word.get("start")
            w_end = raw_word.get("end")
            if not token or w_start is None or w_end is None:
                continue
            if float(w_end) <= float(w_start):
                continue
            words.append(Word(token, float(w_start), float(w_end)))

    words.sort(key=lambda w: w.start)
    segments.sort(key=lambda s: s.start)
    return Transcript(
        language=str(raw.get("language") or language or ""),
        text=" ".join(s.text for s in segments).strip(),
        words=words,
        segments=segments,
        model=model,
    )


def transcribe(
    video: Path,
    cache_dir: Path,
    backend: str,
    model: str = "",
    language: str = "ko",
    on_progress=None,
) -> Transcript:
    """영상 → Transcript. 같은 내용이면 캐시에서 꺼낸다."""
    video = Path(video).expanduser().resolve()
    model = model or default_model(backend)
    key = cache_key(video, model, language)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{key}.json"

    if cached.is_file():
        try:
            if on_progress:
                on_progress("전사 캐시에서 꺼냄")
            return Transcript.from_dict(json.loads(cached.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, ValueError):
            # 깨진 캐시는 지우고 다시 돈다.
            cached.unlink(missing_ok=True)

    wav = cache_dir / f"{key}.wav"
    try:
        if on_progress:
            on_progress("오디오 추출")
        extract_wav(video, wav)

        if on_progress:
            on_progress(f"전사 ({backend} / {model})")
        # 지뢰: numba 가 스레드 안전하지 않다. 동시 호출을 막는다.
        with _ASR_LOCK:
            if backend == "mlx-whisper":
                raw = _transcribe_mlx(wav, model, language)
            else:
                raw = _transcribe_faster(wav, model, language)
    finally:
        wav.unlink(missing_ok=True)

    transcript = normalize(raw, model, language)
    cached.write_text(
        json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return transcript
