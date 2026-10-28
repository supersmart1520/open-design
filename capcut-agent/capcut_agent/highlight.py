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

#: 숏폼 길이. 한 편이 3분 안에 끝나야 혼자서도 이해된다.
MIN_SECONDS = 20.0
MAX_SECONDS = 180.0
#: 기승전결이 편하게 들어가는 길이.
COMFORTABLE = (30.0, 90.0)
#: 추천 개수 상한.
DEFAULT_LIMIT = 8

#: 후킹 신호어. 숏폼에서 첫 3초를 버는 표현들.
HOOK_WORDS = (
    "왜", "비결", "핵심", "가장 중요", "제일 중요", "하지 마", "하지마",
    "실수", "방법", "차이", "이유", "절대", "반드시", "사실", "진짜",
    "결론", "정리하면", "요약", "첫째", "둘째", "셋째", "꿀팁", "추천",
    "주의", "함정", "오해", "착각", "모르면", "아시나요", "아세요",
)

#: 기승전결. 각 단계를 어디서 찾을지(묶음 안의 위치 범위)와 신호어.
#:
#: 사람이 쓰는 말에 "여기가 기(起)" 라는 표시는 없다. 그래서 **자리와
#: 신호어**로 짐작한다 — 결론은 뒤쪽에, 문제 제기는 앞쪽에 오기 마련이다.
#: 짐작이므로 근거를 같이 남겨 사람이 뒤집을 수 있게 한다.
ARC = (
    ("기", (0.0, 0.40), (
        "오늘", "제가", "저는", "우리", "요즘", "여기", "이게", "보시면",
        "한번", "예를 들", "지금", "이번", "있잖아", "하는데", "거든요",
    )),
    ("승", (0.0, 0.70), (
        "왜", "문제", "어려", "안 되", "안돼", "고민", "헷갈", "실수",
        "착각", "오해", "모르", "힘들", "막히", "함정", "?",
    )),
    ("전", (0.20, 0.90), (
        "그래서", "왜냐", "그러면", "그럼", "이렇게", "방법", "즉",
        "따라서", "먼저", "다음", "그다음", "하시면", "보면", "때문",
    )),
    ("결", (0.55, 1.0), (
        "결론", "정리하면", "요약", "결국", "기억", "꼭", "이것만",
        "마지막", "중요한 건", "핵심은", "그러니까", "정리",
    )),
)

#: 웃음·감정 반응. 보는 사람이 웃거나 반응할 만한 자리.
REACTION_WORDS = (
    "ㅋㅋ", "ㅎㅎ", "하하", "웃", "대박", "헐", "진짜요", "어머", "와",
    "아이고", "깜짝", "당황", "민망", "어이없", "반전", "설마", "miss",
)

#: 보고 나서 **가져갈 것**. 기억하거나 공유할 내용.
PAYOFF_WORDS = (
    "팁", "꿀팁", "핵심", "비결", "오해", "착각", "사례", "경험",
    "방법", "순서", "단계", "기준", "차이", "주의", "하지 마", "하지마",
    "추천", "정리", "요약", "체크",
)

#: 인사·지난 시간 복습처럼 **앞을 들어야 이해되는** 시작. 우선순위를 낮춘다.
COLD_START_WORDS = (
    "안녕하세요", "반갑습니다", "지난 시간", "지난번", "저번 시간",
    "이어서", "계속해서", "아까", "앞에서 말씀", "다시 한번 말씀",
)

#: 문장이 끝났다고 볼 신호. 숏폼은 완결된 토막이어야 한다.
_SENTENCE_END = re.compile(r"(다|요|까|죠|네|야|라|습니다|입니다)[.!?]?$|[.!?]$")
_NUMBER = re.compile(r"\d|[일이삼사오육칠팔구십백천만][가-지]?\s*(가지|개|번|단계|초|분)")
#: 주제가 겹치는지 볼 때 쓰는 낱말. 조사까지 떼지는 않는다 — 근사치면 된다.
_WORD = re.compile(r"[가-힣]{2,}")


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
    #: 기승전결 중 찾은 단계 → 그 근거 문장. 왜 이 구간을 가져왔는지의 핵심.
    arc: dict[str, str] = field(default_factory=dict)
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
            "기승전결": self.arc,
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
            arc=dict(raw.get("기승전결", {})),
            selected=bool(raw.get("선택", False)),
        )


def _is_complete(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


def _hooks_in(text: str) -> list[str]:
    lowered = text.strip()
    return [word for word in HOOK_WORDS if word in lowered]


def arc_of(segments: list[Segment]) -> dict[str, str]:
    """기승전결 중 **찾은 단계**와 그 근거 문장.

    네 단계가 다 있으면 앞뒤 맥락을 몰라도 한 편으로 이해된다. 그게
    후보의 1차 관문이다. 못 찾은 단계는 아예 키에 없다 — 없는 걸 빈
    문자열로 남기면 "있는데 비었다" 와 구별되지 않는다.
    """
    if not segments:
        return {}

    found: dict[str, str] = {}
    last = len(segments) - 1
    for stage, (low, high), markers in ARC:
        for number, segment in enumerate(segments):
            where = number / last if last else 0.0
            if not (low <= where <= high):
                continue
            text = segment.text.strip()
            hit = next((m for m in markers if m in text), None)
            if hit is not None:
                found[stage] = text
                break

    # 마지막 문장이 완결되면 결(結)로 친다. 신호어 없이 끝맺는 말도 많다.
    if "결" not in found and _is_complete(segments[-1].text):
        found["결"] = segments[-1].text.strip()
    return found


def _any_in(text: str, words) -> list[str]:
    return [w for w in words if w in text]


def score_window(segments: list[Segment]) -> tuple[float, list[str], dict[str, str]]:
    """후보 한 묶음의 점수·근거·기승전결.

    다섯 기준을 따로 센다. 점수는 **순위용 참고치**지 인기를 보장하는
    수치가 아니다 — 실제 반응은 올려 봐야 안다. 그래서 근거를 같이
    남기고, 고르는 건 사람 몫으로 둔다.
    """
    if not segments:
        return 0.0, [], {}

    text = " ".join(s.text.strip() for s in segments)
    opening = segments[0].text.strip()
    duration = segments[-1].end - segments[0].start
    score = 0.0
    reasons: list[str] = []

    # 1. 3분 안에 기승전결이 있는가 — 가장 무겁게 본다.
    arc = arc_of(segments)
    score += 1.5 * len(arc)
    if len(arc) == 4:
        score += 2.0
        reasons.append("기승전결이 다 있음")
    elif arc:
        reasons.append(f"기승전결 중 {''.join(arc)} 만 있음")
    else:
        reasons.append("기승전결을 찾지 못함")

    # 2. 처음 3초가 궁금한가.
    cold = _any_in(opening, COLD_START_WORDS)
    if cold:
        score -= 2.0
        reasons.append(f"앞을 들어야 이해되는 시작: {cold[0]}")
    hooks = _any_in(opening, HOOK_WORDS)
    if hooks:
        score += 3.0
        reasons.append(f"첫 문장에 후킹 표현: {', '.join(hooks[:3])}")
    elif not cold:
        later = _any_in(text, HOOK_WORDS)
        if later:
            score += 1.0
            reasons.append(f"본문에 후킹 표현: {', '.join(later[:3])}")
    if "?" in opening:
        score += 1.5
        reasons.append("질문으로 시작")

    # 3. 웃음이나 감정 반응이 있는가.
    reactions = _any_in(text, REACTION_WORDS)
    if reactions:
        score += 2.0
        reasons.append(f"감정 반응: {', '.join(reactions[:3])}")

    # 4. 보는 사람이 얻어갈 게 있는가.
    payoff = _any_in(text, PAYOFF_WORDS)
    if payoff:
        score += 2.0
        reasons.append(f"가져갈 내용: {', '.join(payoff[:3])}")
    if _NUMBER.search(text):
        score += 1.0
        reasons.append("숫자·개수 표현 포함")

    # 5. 보기·듣기 좋은가 — 여기서는 **말소리만** 본다.
    #    음악이 말을 덮는지, 인물이 잘 보이는지는 재지 못한다. 재지 못하는
    #    것을 잰 척하지 않는다.
    density = len(text.replace(" ", "")) / max(duration, 1e-3)
    if 3.0 <= density <= 7.0:
        score += 1.0
        reasons.append(f"말 밀도 적정 ({density:.1f}자/초)")
    elif density < 2.0:
        score -= 1.0
        reasons.append(f"말이 비어있는 구간 많음 ({density:.1f}자/초)")

    if not _is_complete(segments[-1].text):
        score -= 1.5
        reasons.append("끝이 잘린 느낌")
    if COMFORTABLE[0] <= duration <= COMFORTABLE[1]:
        score += 1.0
        reasons.append(f"길이 {COMFORTABLE[0]:.0f}~{COMFORTABLE[1]:.0f}초")
    elif duration < 22.0:
        score -= 0.5
        reasons.append("다소 짧음")

    return score, reasons, arc


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


def _topic(text: str) -> set[str]:
    """주제를 가늠할 낱말 모음. 같은 얘기가 반복되는 걸 막는 데 쓴다."""
    return set(_WORD.findall(text))


def _too_similar(topic: set[str], taken: list[set[str]], limit: float = 0.5) -> bool:
    """이미 고른 것과 주제가 겹치나. 겹치면 다른 얘기를 찾는 게 낫다."""
    for other in taken:
        union = topic | other
        if union and len(topic & other) / len(union) > limit:
            return True
    return False


def propose(
    transcript: Transcript,
    min_seconds: float = MIN_SECONDS,
    max_seconds: float = MAX_SECONDS,
    limit: int = DEFAULT_LIMIT,
) -> list[Candidate]:
    """대본을 훑어 후보를 추천한다.

    차례는 이렇다.

        1. 길이 안에 드는 묶음을 전부 만든다
        2. **기승전결이 다 있는 것**만 먼저 본다 — 혼자서도 이해되는 조건
        3. 점수 순으로 집되, 구간이 겹치거나 **주제가 겹치면** 건너뛴다
        4. 그래도 모자라면 기승전결이 덜 갖춰진 것에서 채운다

    4번이 있는 이유: 기준을 못 채웠다고 빈손으로 돌려주면, 사람이 볼
    재료조차 없어진다. 대신 근거에 무엇이 빠졌는지 적어 둔다.
    """
    segments = transcript.segments
    if not segments:
        return []

    windows = []
    for i in range(len(segments)):
        group: list[Segment] = []
        for j in range(i, len(segments)):
            group.append(segments[j])
            duration = segments[j].end - segments[i].start
            if duration < min_seconds:
                continue
            if duration > max_seconds:
                break
            score, reasons, arc = score_window(group)
            windows.append((score, list(group), i, j, reasons, arc))

    windows.sort(key=lambda w: (-w[0], w[2]))

    taken: list[tuple[int, int]] = []
    topics: list[set[str]] = []
    chosen = []

    def collect(only_complete: bool) -> None:
        for score, group, i, j, reasons, arc in windows:
            if len(chosen) >= limit:
                return
            if only_complete and len(arc) < 4:
                continue
            if any(not (j < a or i > b) for a, b in taken):
                continue
            text = " ".join(s.text for s in group)
            topic = _topic(text)
            if _too_similar(topic, topics):
                continue
            taken.append((i, j))
            topics.append(topic)
            chosen.append((score, group, reasons, arc))

    collect(only_complete=True)
    if len(chosen) < limit:
        collect(only_complete=False)

    # 결과는 영상 순서대로 내보낸다. 사람이 훑기 쉽게.
    chosen.sort(key=lambda c: c[1][0].start)
    candidates: list[Candidate] = []
    for number, (score, group, reasons, arc) in enumerate(chosen, 1):
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
                arc=arc,
            )
        )
    return candidates


def representative_line(transcript: Transcript, max_chars: int = 26) -> str:
    """영상을 대표하는 한 줄. 제목 자막의 기본값.

    후킹 표현이 들어간 짧고 완결된 문장을 고른다. 없으면 첫 문장을 줄여 쓴다.
    사람이 `--title` 로 덮어쓰는 게 늘 낫고, 이건 그 전의 그럴듯한 기본값이다.
    """
    segments = transcript.segments
    if not segments:
        return ""

    best, best_score = "", float("-inf")
    for segment in segments[: min(len(segments), 40)]:
        line = " ".join(segment.text.split())
        if not line:
            continue
        score = 0.0
        hooks = _hooks_in(line)
        score += 2.0 * min(len(hooks), 2)
        if "?" in line:
            score += 1.5
        if _is_complete(line):
            score += 1.0
        # 제목은 한 줄이어야 하므로 짧을수록 좋다.
        score -= abs(len(line) - max_chars) / max_chars
        if score > best_score:
            best, best_score = line, score

    if not best:
        best = " ".join(segments[0].text.split())
    return best if len(best) <= max_chars else best[: max_chars - 1] + "…"


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
