"""제목 자막 규칙 — 릴스 썸네일 문법.

제목은 영상당 **한 장, 두 줄**이다. 말하는 자막과 목적이 다르다. 자막은 들은
말을 받아 적지만, 제목은 **썸네일에서 손가락을 멈추게** 하는 문구다.

카피 원칙 (《스틱!》 SUCCES 중 썸네일에 직결되는 셋)

  단순성   문장이 아니라 **명사로 끝낸다.** 조사와 미사여구를 뗀다.
  구체성   모호한 형용사 대신 숫자·기간·대상을 박는다.
  의외성   '이유', '비결', '방법' 으로 2행을 닫아 지식 공백을 만든다.

행 구성

  1행  타깃·상황 선점    4~8자   타깃 명사 또는 D-Day   강조색
  2행  솔루션·호기심     5~10자  숫자+단위 또는 이유·방법 명사   흰색

**절대 규칙: 모든 행은 동사 서술형이 아니라 명사·체언으로 끝낸다.**
"~합니다", "~해보세요" 는 썸네일에서 길고 약하다.

크기 계층

  표준형    두 행 길이가 비슷하면 100% : 100%
  2행 확장형 2행이 9자 이상이거나 1행보다 확연히 길면 1행 100% : 2행 80%
            1행이 헤드라인으로 뜨고 2행이 서브헤드라인처럼 받친다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 행별 글자 수 (공백 포함).
LINE1_MIN, LINE1_MAX = 4, 8
LINE2_MIN, LINE2_MAX = 5, 10

#: 2행이 이보다 길면 축소한다.
EXPAND_THRESHOLD = 9
#: 2행이 1행보다 이만큼 길어도 축소한다.
EXPAND_DELTA = 3
#: 축소 비율.
SHRINK = 0.80

#: 제목 전체 최대 글자 수 (두 행 합).
DEFAULT_MAX_CHARS = LINE1_MAX + LINE2_MAX


@dataclass(frozen=True)
class Palette:
    """제목 2색 조합. 외곽선은 항상 검정 — 어떤 옷 색 위에서도 분리된다."""

    name: str
    accent: str          # 1행 — 시선이 먼저 꽂히는 자리
    plain: str = "#FFFFFF"   # 2행
    stroke: str = "#000000"


PALETTES = {
    "green": Palette("green", "#22C55E"),
    "red": Palette("red", "#EF4444"),
}
DEFAULT_PALETTE = "green"

#: 감싸는 기호. 양쪽에서 벗긴다.
_WRAPPERS = "\"'“”‘’()[]{}«»「」『』<>"
#: 끝에서 떼는 기호. 물음표·느낌표는 후킹이라 남긴다.
_TRAILING_NOISE = ".,·…:;~-—"

_WHITESPACE = re.compile(r"\s+")

#: 동사 서술형 종결. 썸네일에서는 쓰지 않는다 — 명사로 끊는다.
_VERB_ENDING = re.compile(
    r"(니다|세요|해라|하자|했다|한다|"
    # -아요/-어요/-여요 계열. 이 꼴로 끝나는 보통명사는 사실상 없다.
    r"[아어여]요|"
    r"돼요|예요|이에요)$"
)

#: 끝에 붙은 조사. 명사형으로 끊기 위해 뗀다.
_PARTICLE = re.compile(r"(을|를|이|가|은|는|의|에|에서|으로|로|와|과|도|만)$")


def is_noun_ending(line: str) -> bool:
    """명사·체언으로 끝나는가. 동사 서술형이면 False."""
    text = line.strip().rstrip("!?")
    return bool(text) and not _VERB_ENDING.search(text)


def strip_particle(word: str) -> str:
    """끝 조사를 뗀다. 두 글자 이상 남을 때만 — '의' 를 떼서 한 글자가 되면 곤란하다."""
    stripped = _PARTICLE.sub("", word)
    return stripped if len(stripped) >= 2 else word


def _normalize(text: str) -> str:
    line = _WHITESPACE.sub(" ", str(text or "")).strip()
    while line and line[0] in _WRAPPERS:
        line = line[1:].strip()
    while line and line[-1] in _WRAPPERS:
        line = line[:-1].strip()
    return line.rstrip(_TRAILING_NOISE).strip()


@dataclass(frozen=True)
class Title:
    """두 줄 제목과 그 크기 계층."""

    line1: str
    line2: str
    palette: Palette

    @property
    def text(self) -> str:
        return f"{self.line1}\n{self.line2}" if self.line2 else self.line1

    @property
    def shrink_line2(self) -> bool:
        """2행을 줄여야 하는가 (Case B)."""
        if not self.line2:
            return False
        return (
            len(self.line2) >= EXPAND_THRESHOLD
            or len(self.line2) - len(self.line1) >= EXPAND_DELTA
        )

    @property
    def line2_scale(self) -> float:
        return SHRINK if self.shrink_line2 else 1.0

    @property
    def warnings(self) -> list[str]:
        """규칙에서 벗어난 점. 막지는 않고 알려준다.

        동사 서술형 종결은 **경고하지 않는다.** 레퍼런스 썸네일이
        "아이한테 하지마세요!" 처럼 쓰고 있고, 실제로 잘 먹힌다.
        `is_noun_ending()` 은 판정용으로 남겨 두되 경고 근거로는 쓰지 않는다.
        """
        notes = []
        if not (LINE1_MIN <= len(self.line1) <= LINE1_MAX):
            notes.append(
                f"1행 {len(self.line1)}자 (권장 {LINE1_MIN}~{LINE1_MAX})"
            )
        if self.line2 and not (LINE2_MIN <= len(self.line2) <= LINE2_MAX):
            notes.append(
                f"2행 {len(self.line2)}자 (권장 {LINE2_MIN}~{LINE2_MAX})"
            )
        return notes


def split_title(text: str) -> tuple[str, str]:
    """한 덩어리 문구를 1행 / 2행으로 가른다.

    사용자가 `\\n` 으로 직접 나눴으면 그대로 쓴다. 아니면 1행이 권장 범위
    안에 들어오도록 어절 경계에서 가른다.
    """
    normalized = _normalize(text)
    if not normalized:
        return "", ""

    if "\n" in str(text):
        parts = [_normalize(part) for part in str(text).split("\n", 1)]
        return parts[0], (parts[1] if len(parts) > 1 else "")

    words = normalized.split()
    if len(words) == 1:
        return strip_particle(words[0]), ""

    best, best_cost = 1, None
    for cut in range(1, len(words)):
        first = " ".join(words[:cut])
        second = " ".join(words[cut:])
        cost = _fit_cost(len(first), LINE1_MIN, LINE1_MAX)
        cost += _fit_cost(len(second), LINE2_MIN, LINE2_MAX)
        if best_cost is None or cost < best_cost:
            best, best_cost = cut, cost

    first = " ".join(words[:best])
    second = " ".join(words[best:])
    return strip_particle(first), second


def _fit_cost(length: int, low: int, high: int) -> int:
    """권장 범위에서 벗어난 정도. 가를 자리를 고르는 기준."""
    if length < low:
        return low - length
    if length > high:
        return (length - high) * 2      # 넘치는 쪽이 더 나쁘다
    return 0


def make_title(
    text: str,
    palette: str = DEFAULT_PALETTE,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Title | None:
    """문구에 제목 규칙을 적용한다. 쓸 수 없으면 None."""
    line1, line2 = split_title(text)
    if not line1:
        # 빈 텍스트 세그먼트는 캡컷에서 빈 상자로 남는다.
        return None

    if len(line1) + len(line2) > max_chars:
        line2 = _trim(line2, max(0, max_chars - len(line1)))
    return Title(line1, line2, PALETTES.get(palette, PALETTES[DEFAULT_PALETTE]))


def _trim(line: str, budget: int) -> str:
    """어절 경계에서 줄인다. 글자 중간에서 자르면 읽다 막힌다."""
    if budget <= 0 or not line:
        return ""
    if len(line) <= budget:
        return line
    words = line.split()
    kept: list[str] = []
    for word in words:
        candidate = " ".join([*kept, word])
        if len(candidate) > budget:
            break
        kept.append(word)
    return " ".join(kept) if kept else line[:budget]
