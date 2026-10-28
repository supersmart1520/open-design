"""한국어 자막 — 줄바꿈과 큐 묶기.

세로 숏폼 자막은 한 줄 18자, 두 줄이 한계다. 그 안에서 **어디서 끊는지**가
읽히는 자막과 안 읽히는 자막을 가른다.

지뢰: 의존명사는 혼자 줄을 시작할 수 없다.

    나쁜 줄바꿈          좋은 줄바꿈
    ─────────────        ─────────────
    이걸 할              이걸
    수 있습니다          할 수 있습니다

"수", "것", "때", "거" 같은 의존명사는 앞 어절에 붙어야 뜻이 산다.
`wrap()` 이 그런 줄을 발견하면 앞 어절을 아래로 끌어내린다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 한 줄 기본 글자 수. 1080 폭 9:16 에서 읽히는 한계.
DEFAULT_MAX_CHARS = 18
#: 한 큐의 최대 줄 수.
DEFAULT_MAX_LINES = 2
#: 이만큼 쉬면 큐를 끊는다 (초).
DEFAULT_GAP_BREAK = 0.5

#: 혼자 줄을 시작하면 안 되는 의존명사.
BOUND_NOUNS = (
    "것", "수", "때", "거", "바", "줄", "리", "지", "데", "뿐", "만큼",
    "채", "척", "체", "양", "등", "터", "셈", "법", "김", "통", "따름", "나위",
)

#: 의존명사에 붙는 조사/어미. 빈 문자열은 조사 없이 홀로 쓰인 경우.
_PARTICLES = (
    "", "이", "가", "을", "를", "은", "는", "에", "의", "도", "만", "로",
    "으로", "과", "와", "에서", "부터", "까지", "이다", "예요", "이에요",
    "입니다", "라", "야", "요", "다", "든", "에는", "에도", "이야", "이죠",
    "죠", "지요", "인데", "이라", "라고", "이라고",
)

#: 의존명사 + 조사 조합. 이 집합에 정확히 들어맞을 때만 의존명사로 본다.
#: "데이터", "거리" 처럼 우연히 같은 글자로 시작하는 보통명사를 걸러낸다.
_BOUND_TOKENS = frozenset(
    f"{noun}{particle}" for noun in BOUND_NOUNS for particle in _PARTICLES
) | frozenset({"때문에", "때문이", "때문으로", "때문입니다", "덕분에", "탓에"})

_SENTENCE_END = re.compile(r"[.!?。！？]$")
_TRIM = re.compile(r"^[\s\"'“”‘’(\[{]+|[\s\"'“”‘’)\]}]+$")


@dataclass(frozen=True)
class SubtitleCue:
    """타임라인(컷 적용 후) 기준 자막 한 장."""

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def lines(self) -> list[str]:
        return self.text.split("\n")


@dataclass(frozen=True)
class Word:
    """전사에서 나온 단어 하나. 시간은 소스 기준 초."""

    text: str
    start: float
    end: float


def _bare(token: str) -> str:
    """구두점·따옴표를 벗긴 알맹이."""
    return _TRIM.sub("", token)


def is_bound_noun(token: str) -> bool:
    """이 어절이 혼자 줄을 시작하면 안 되는 의존명사인가."""
    return _bare(token) in _BOUND_TOKENS


def wrap(text: str, max_chars: int = DEFAULT_MAX_CHARS,
         max_lines: int = DEFAULT_MAX_LINES) -> list[str]:
    """어절 단위로 줄을 채우고, 의존명사로 시작하는 줄을 고친다.

    max_lines 를 넘는 분량이 들어오면 넘치는 줄도 그대로 돌려준다.
    큐를 쪼개는 건 `split_text` 의 일이다.
    """
    tokens = _splittable_tokens(text.split(), max_chars)
    if not tokens:
        return []

    lines: list[list[str]] = [[]]
    for token in tokens:
        current = lines[-1]
        projected = len(" ".join([*current, token]))
        if current and projected > max_chars:
            lines.append([token])
        else:
            current.append(token)

    _fix_bound_nouns(lines, max_chars)
    return [" ".join(line) for line in lines if line]


def _splittable_tokens(tokens: list[str], max_chars: int) -> list[str]:
    """한 줄보다 긴 어절은 강제로 쪼갠다.

    어절을 쪼개는 건 원칙적으로 피하지만, 한 어절이 줄 예산보다 길면
    선택지가 둘뿐이다. 쪼개서 보여주거나, 넘쳐서 캡컷에서 잘려 안 보이거나.
    전사 결과에는 거의 없고 URL·숫자 나열에서 나온다.
    """
    out: list[str] = []
    for token in tokens:
        if len(token) <= max_chars:
            out.append(token)
            continue
        out.extend(token[i : i + max_chars] for i in range(0, len(token), max_chars))
    return out


def _fix_bound_nouns(lines: list[list[str]], max_chars: int) -> None:
    """의존명사로 시작하는 줄의 앞 어절을 그 줄로 끌어내린다.

    앞 줄이 어절 하나뿐이면 끌어내릴 수 없다 (앞 줄이 비어버린다). 그대로 둔다.
    """
    for i in range(1, len(lines)):
        line = lines[i]
        previous = lines[i - 1]
        if not line or not is_bound_noun(line[0]):
            continue
        if len(previous) < 2:
            continue
        moved = previous[-1]
        if len(" ".join([moved, *line])) > max_chars:
            # 끌어내리면 줄이 터진다. 터지는 쪽보다 뜻이 끊기는 쪽이 낫다.
            continue
        previous.pop()
        line.insert(0, moved)


def split_text(text: str, max_chars: int = DEFAULT_MAX_CHARS,
               max_lines: int = DEFAULT_MAX_LINES) -> list[str]:
    """한 큐에 안 들어가는 긴 문장을 여러 큐로 쪼갠다. 각 원소는 줄바꿈 포함."""
    lines = wrap(text, max_chars, max_lines)
    if not lines:
        return []
    return [
        "\n".join(lines[i : i + max_lines])
        for i in range(0, len(lines), max_lines)
    ]


def cues_from_words(
    words: list[Word],
    max_chars: int = DEFAULT_MAX_CHARS,
    max_lines: int = DEFAULT_MAX_LINES,
    gap_break: float = DEFAULT_GAP_BREAK,
) -> list[SubtitleCue]:
    """단어 타임스탬프를 자막 큐로 묶는다.

    끊는 기준은 셋이다. 문장이 끝났을 때, 쉬었을 때, 분량이 찼을 때.
    """
    budget = max_chars * max_lines
    cues: list[SubtitleCue] = []
    buffer: list[Word] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        raw = " ".join(w.text.strip() for w in buffer if w.text.strip())
        start, end = buffer[0].start, buffer[-1].end
        buffer = []
        chunks = split_text(raw, max_chars, max_lines)
        if not chunks:
            return
        # 쪼개진 큐들은 원래 구간을 글자 수 비례로 나눠 가진다.
        total_chars = sum(len(c.replace("\n", "")) for c in chunks) or 1
        cursor = start
        span = max(end - start, 1e-3)
        for chunk in chunks:
            share = len(chunk.replace("\n", "")) / total_chars
            chunk_end = min(end, cursor + span * share)
            if chunk_end > cursor:
                cues.append(SubtitleCue(cursor, chunk_end, chunk))
            cursor = chunk_end

    for word in words:
        if not word.text.strip():
            continue
        if buffer:
            previous = buffer[-1]
            gap = word.start - previous.end
            projected = len(" ".join([*(w.text.strip() for w in buffer), word.text.strip()]))
            if (
                gap >= gap_break
                or projected > budget
                or _SENTENCE_END.search(previous.text.strip())
            ):
                flush()
        buffer.append(word)
    flush()
    return cues


def to_srt(cues: list[SubtitleCue]) -> str:
    """캡컷 밖에서도 쓸 수 있게 SRT 로. 드래프트가 안 열릴 때의 탈출구."""
    blocks = []
    for i, cue in enumerate(cues, 1):
        blocks.append(
            f"{i}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n{cue.text}\n"
        )
    return "\n".join(blocks)


def _srt_time(seconds: float) -> str:
    total_ms = int(round(max(seconds, 0.0) * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
