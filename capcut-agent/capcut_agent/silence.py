"""무음 구간 → 보존 구간(keep) 계산. 순수 로직만 두어 단위테스트가 가능하다."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


@dataclass(frozen=True)
class Range:
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass
class SilenceOptions:
    #: 이보다 조용하면 무음으로 본다 (dBFS).
    noise_db: float = -34.0
    #: 이보다 짧은 무음은 무시한다 (초).
    min_silence: float = 0.35
    #: 보존 구간 앞뒤로 남길 여유 (초). 숨소리가 잘려 딱딱해지는 걸 막는다.
    padding: float = 0.08
    #: 이보다 짧은 보존 구간은 버린다 (초).
    min_keep: float = 0.30
    #: 이보다 가까운 보존 구간끼리는 붙인다 (초). 컷 수를 줄인다.
    min_gap: float = 0.12

    def to_dict(self) -> dict:
        return asdict(self)


def parse_silence(stderr: str, duration: float) -> list[Range]:
    """silencedetect 출력 파싱.

    끝에 silence_end 없이 silence_start 만 남으면 파일이 무음으로 끝난 것이다.
    """
    ranges: list[Range] = []
    open_at: float | None = None
    for line in stderr.splitlines():
        m = _START_RE.search(line)
        if m:
            open_at = max(0.0, float(m.group(1)))
            continue
        m = _END_RE.search(line)
        if m and open_at is not None:
            end = min(duration, float(m.group(1)))
            if end > open_at:
                ranges.append(Range(open_at, end))
            open_at = None
    if open_at is not None and duration > open_at:
        ranges.append(Range(open_at, duration))
    return ranges


def merge(ranges: list[Range], tolerance: float = 0.0) -> list[Range]:
    out: list[Range] = []
    for r in sorted(ranges, key=lambda x: x.start):
        if r.length <= 0:
            continue
        if out and r.start - out[-1].end <= tolerance:
            out[-1] = Range(out[-1].start, max(out[-1].end, r.end))
        else:
            out.append(r)
    return out


def invert(drops: list[Range], duration: float) -> list[Range]:
    """[0, duration] 에서 drops 를 뺀 나머지."""
    keeps: list[Range] = []
    cursor = 0.0
    for drop in merge(drops):
        if drop.start > cursor:
            keeps.append(Range(cursor, min(drop.start, duration)))
        cursor = max(cursor, drop.end)
    if cursor < duration:
        keeps.append(Range(cursor, duration))
    return [k for k in keeps if k.length > 0]


def pad_and_tidy(keeps: list[Range], duration: float, opts: SilenceOptions) -> list[Range]:
    """여유를 붙이고(이웃을 침범하지 않게), 너무 가까운 건 합치고, 너무 짧은 건 버린다."""
    padded: list[Range] = []
    for i, keep in enumerate(keeps):
        prev_end = 0.0 if i == 0 else keeps[i - 1].end
        next_start = duration if i == len(keeps) - 1 else keeps[i + 1].start
        room_before = max((keep.start - prev_end) / 2, 0.0)
        room_after = max((next_start - keep.end) / 2, 0.0)
        padded.append(
            Range(
                max(0.0, keep.start - min(opts.padding, room_before)),
                min(duration, keep.end + min(opts.padding, room_after)),
            )
        )
    return [k for k in merge(padded, opts.min_gap) if k.length >= opts.min_keep]


def to_timeline(keeps: list[Range], source_time: float) -> float | None:
    """소스 시각 → 컷 적용 후 타임라인 시각. 잘려나간 자리면 None."""
    offset = 0.0
    for keep in keeps:
        if source_time < keep.start:
            return None
        if source_time <= keep.end:
            return offset + (source_time - keep.start)
        offset += keep.length
    return None


def keeps_from_words(words, duration: float, opts: SilenceOptions) -> list[Range]:
    """**대본**에서 보존 구간을 만든다. 무음 감지 대신 whisper 가 컷을 정한다.

    ffmpeg 의 무음 감지는 소리의 크기만 본다. 단어가 어디서 시작하고 끝나는지
    모르니 컷 경계가 단어 한가운데 떨어질 수 있고, 그러면 말이 잘리거나
    자막이 사라진다. 단어 타임스탬프로 자르면 컷이 **항상 단어 사이**에 떨어진다.

    규칙은 하나다. 단어와 단어 사이 틈이 min_silence 보다 길면 거기를 자른다.
    """
    spans = [Range(w.start, w.end) for w in words if w.end > w.start]
    if not spans:
        return []
    # 틈이 min_silence 이하면 같은 덩어리로 본다.
    merged = merge(spans, tolerance=opts.min_silence)
    return pad_and_tidy(merged, duration, opts)


def overlap_to_timeline(
    keeps: list[Range], start: float, end: float
) -> tuple[float, float] | None:
    """단어 구간 [start, end] 중 **살아남은 부분**을 타임라인 시각으로.

    to_timeline() 은 한 점이 잘려나간 자리면 None 을 준다. 그걸 단어에 그대로
    쓰면 컷 경계에 걸친 단어가 통째로 버려진다. 무음 경계는 단어 경계와
    일치하지 않으므로, 말이 시작·끝나는 지점의 단어가 바로 그렇게 사라진다.
    영상에는 말이 남았는데 자막만 없는 구간이 생기는 원인이다.

    그래서 버리는 대신 **겹치는 부분만 잘라 쓴다.** 보존 구간과 전혀 겹치지
    않는 단어(= 소리가 통째로 사라진 단어)만 None 이다.
    """
    if end < start:
        start, end = end, start
    if end == start:
        # 길이 0 토막. 한 점이라도 보존 구간 안이면 살린다.
        point = to_timeline(keeps, start)
        return None if point is None else (point, point)

    first: float | None = None
    last: float | None = None
    offset = 0.0
    for keep in keeps:
        low = max(start, keep.start)
        high = min(end, keep.end)
        if high > low:
            if first is None:
                first = offset + (low - keep.start)
            last = offset + (high - keep.start)
        offset += keep.length

    if first is None or last is None or last <= first:
        return None
    return first, last


def keeps_from_silence(stderr: str, duration: float, opts: SilenceOptions) -> list[Range]:
    """silencedetect 출력 한 방에서 최종 보존 구간까지."""
    silences = parse_silence(stderr, duration)
    return pad_and_tidy(invert(list(silences), duration), duration, opts)
