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


def keeps_from_silence(stderr: str, duration: float, opts: SilenceOptions) -> list[Range]:
    """silencedetect 출력 한 방에서 최종 보존 구간까지."""
    silences = parse_silence(stderr, duration)
    return pad_and_tidy(invert(list(silences), duration), duration, opts)
