"""강의 대본에서 숏폼 후보 구간 고르기.

흐름은 세 걸음이다.

    1. 전사 → 대본 (transcribe.py)
    2. 후보 추천 → scripts/<이름>.shorts.json   ← 여기까지가 이 모듈
    3. 사람이 "선택": true 를 켠 것만 드래프트로

'유효한 부분' 은 결국 사람 판단이다. 그래서 코드는 **고르지 않고 추천만**
하고, 왜 추천했는지(근거)를 같이 적어 판단 재료를 준다. 교환 파일은
사람이 손으로 고치기 쉽게 한글 키를 쓴다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .subtitle import Word
from .transcribe import Segment, Transcript

#: 숏폼 길이. 이 범위를 벗어나는 묶음은 후보로 보지 않는다.
MIN_SECONDS = 18.0
MAX_SECONDS = 60.0
#: 추천 개수 상한.
DEFAULT_LIMIT = 8

#: 후킹 신호어. 숏폼에서 첫 2초를 버는 표현들.
HOOK_WORDS = (
    "왜", "비결", "핵심", "가장 중요", "제일 중요", "하지 마", "하지마",
    "실수", "방법", "차이", "이유", "절대", "반드시", "사실", "진짜",
    "결론", "정리하면", "요약", "첫째", "둘째", "셋째", "꿀팁", "추천",
    "주의", "함정", "오해", "착각", "모르면", "아시나요", "아세요",
)

#: 문장이 끝났다고 볼 신호. 숏폼은 완결된 토막이어야 한다.
_SENTENCE_END = re.compile(r"(다|요|까|죠|네|야|라|습니다|입니다)[.!?]?$|[.!?]$")
_NUMBER = re.compile(r"\d|[일이삼사오육칠팔구십백천만][가-지]?\s*(가지|개|번|단계|초|분)")


@dataclass
class Candidate:
    """숏폼 후보 한 토막."""

    index: int
    start: float
    end: float
    title: str
    text: str
    score: float
    reasons: list[str] = field(default_factory=list)
    selected: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "번호": self.index,
            "선택": self.selected,
            "시작": round(self.start, 2),
            "끝": round(self.end, 2),
            "길이": round(self.duration, 1),
            "제목": self.title,
            "점수": round(self.score, 1),
            "근거": self.reasons,
            "대본": self.text,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Candidate:
        return cls(
            index=int(raw.get("번호", 0)),
            start=float(raw["시작"]),
            end=float(raw["끝"]),
            title=str(raw.get("제목", "")),
            text=str(raw.get("대본", "")),
            score=float(raw.get("점수", 0.0)),
            reasons=list(raw.get("근거", [])),
            selected=bool(raw.get("선택", False)),
        )


def _is_complete(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


def _hooks_in(text: str) -> list[str]:
    lowered = text.strip()
    return [word for word in HOOK_WORDS if word in lowered]


def score_window(segments: list[Segment]) -> tuple[float, list[str]]:
    """후보 한 묶음의 점수와 근거. 점수는 순위용이고, 판단은 사람이 한다."""
    if not segments:
        return 0.0, []

    text = " ".join(s.text.strip() for s in segments)
    duration = segments[-1].end - segments[0].start
    score = 0.0
    reasons: list[str] = []

    hooks = _hooks_in(segments[0].text)
    if hooks:
        score += 3.0
        reasons.append(f"첫 문장에 후킹 표현: {', '.join(hooks[:3])}")
    else:
        later = _hooks_in(text)
        if later:
            score += 1.0
            reasons.append(f"본문에 후킹 표현: {', '.join(later[:3])}")

    if "?" in segments[0].text:
        score += 1.5
        reasons.append("질문으로 시작")

    if _is_complete(segments[-1].text):
        score += 2.0
        reasons.append("완결된 문장으로 끝남")
    else:
        score -= 1.5
        reasons.append("끝이 잘린 느낌")

    if _NUMBER.search(text):
        score += 1.0
        reasons.append("숫자·개수 표현 포함")

    # 30~45초가 숏폼에서 가장 안정적이다.
    if 30.0 <= duration <= 45.0:
        score += 1.5
        reasons.append("길이 30~45초")
    elif duration < 22.0:
        score -= 0.5
        reasons.append("다소 짧음")

    # 말 밀도. 너무 느리면 늘어지고, 너무 빠르면 자막이 안 따라온다.
    density = len(text.replace(" ", "")) / max(duration, 1e-3)
    if 3.0 <= density <= 7.0:
        score += 1.0
        reasons.append(f"말 밀도 적정 ({density:.1f}자/초)")
    elif density < 2.0:
        score -= 1.0
        reasons.append(f"말이 비어있는 구간 많음 ({density:.1f}자/초)")

    return score, reasons


def _title_for(segments: list[Segment]) -> str:
    """첫 문장을 제목으로. 너무 길면 자른다."""
    head = segments[0].text.strip()
    head = re.sub(r"\s+", " ", head)
    return head if len(head) <= 40 else head[:39] + "…"


def snap_to_words(start: float, end: float, words: list[Word],
                  slack: float = 0.25) -> tuple[float, float]:
    """구간 경계를 단어 경계로 당긴다. 말 중간에서 잘리는 걸 막는다.

    단어 타임스탬프가 없으면(전사가 못 준 경우) 그대로 돌려준다.
    """
    if not words:
        return start, end

    inside = [w for w in words if w.end > start - slack and w.start < end + slack]
    if not inside:
        return start, end
    return min(w.start for w in inside), max(w.end for w in inside)


def propose(
    transcript: Transcript,
    min_seconds: float = MIN_SECONDS,
    max_seconds: float = MAX_SECONDS,
    limit: int = DEFAULT_LIMIT,
) -> list[Candidate]:
    """대본을 훑어 겹치지 않는 후보를 점수 순으로 추천한다."""
    segments = transcript.segments
    if not segments:
        return []

    windows: list[tuple[float, list[Segment], int, int]] = []
    for i in range(len(segments)):
        group: list[Segment] = []
        for j in range(i, len(segments)):
            group.append(segments[j])
            duration = segments[j].end - segments[i].start
            if duration < min_seconds:
                continue
            if duration > max_seconds:
                break
            score, _ = score_window(group)
            windows.append((score, list(group), i, j))

    # 점수 높은 순으로 집되, 이미 집은 구간과 겹치면 버린다.
    windows.sort(key=lambda w: (-w[0], w[2]))
    taken: list[tuple[int, int]] = []
    chosen: list[tuple[float, list[Segment]]] = []
    for score, group, i, j in windows:
        if any(not (j < a or i > b) for a, b in taken):
            continue
        taken.append((i, j))
        chosen.append((score, group))
        if len(chosen) >= limit:
            break

    # 결과는 영상 순서대로 내보낸다. 사람이 훑기 쉽게.
    chosen.sort(key=lambda c: c[1][0].start)
    candidates: list[Candidate] = []
    for number, (score, group) in enumerate(chosen, 1):
        _, reasons = score_window(group)
        start, end = snap_to_words(group[0].start, group[-1].end, transcript.words)
        candidates.append(
            Candidate(
                index=number,
                start=start,
                end=end,
                title=_title_for(group),
                text=" ".join(s.text.strip() for s in group),
                score=score,
                reasons=reasons,
            )
        )
    return candidates


def save_candidates(
    path: Path, source: Path, transcript: Transcript, candidates: list[Candidate]
) -> Path:
    """승인 교환 파일. '선택' 을 true 로 바꾼 것만 드래프트가 된다."""
    payload = {
        "_안내": "쓸 후보의 \"선택\" 을 true 로 바꾸고 저장하세요. "
                 "시작·끝을 직접 고쳐도 됩니다 (초 단위).",
        "원본": str(source),
        "언어": transcript.language,
        "모델": transcript.model,
        "원본길이초": round(transcript.duration, 2),
        "후보": [c.to_dict() for c in candidates],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_candidates(path: Path) -> tuple[Path, list[Candidate]]:
    """교환 파일을 다시 읽는다. (원본 경로, 후보들)"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    source = Path(str(raw.get("원본", "")))
    candidates = [Candidate.from_dict(item) for item in raw.get("후보", [])]
    return source, candidates


def selected(candidates: list[Candidate]) -> list[Candidate]:
    return [c for c in candidates if c.selected and c.duration > 0]
