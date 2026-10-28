"""whisper 토막 → 한국어 어절 복원.

whisper 의 word timestamp 는 한국어에서 어절(단어+조사)을 자주 쪼갠다.
"있습니" / "다", "순서" / "가" 처럼 갈라지면 자막 줄바꿈이 전부 어긋난다.

복원 단서는 whisper BPE 의 **leading space** 다. 새 어절을 시작하는 토막에는
앞에 공백(' ' 또는 SentencePiece 의 '▁')이 붙고, 이어지는 토막에는 붙지 않는다.
그래서 선행 공백이 없는 토막을 앞 어절에 이어붙이면 어절이 복원된다.

이 접근은 dawn-cut (MIT, https://github.com/kwakseongjae/dawn-cut) 의
`packages/core/src/whisper.ts` 에서 가져왔다. 그쪽이 whisper.cpp 자연모드
JSON 을 다루며 적대검증으로 찾아낸 불변식들을 여기서도 지킨다.

  * 순수 구두점은 **독립 어절이 되지 않고, 절대 버려지지 않는다.**
    여는 부호(선행 공백 있음)는 다음 어절 앞에 붙고, 닫는 부호는 앞 어절에 흡수된다.
  * 특수 토큰([_BEG_], <|endoftext|> 류)은 건너뛴다.
  * 시작 시각은 단조 증가를 보장한다. 비단조 offset 이 섞여 와도 순서가 뒤집히지 않는다.
  * 길이 0 토막은 최소 길이를 준다. end > start 를 항상 지킨다.
  * 빈 텍스트는 버린다.

NOTICE 에 출처를 적어 두었다.
"""

from __future__ import annotations

import re
import unicodedata

from .subtitle import Word

#: 새 어절의 시작을 알리는 선행 공백. SentencePiece 의 '▁'(U+2581) 포함.
_LEADING_SPACE = re.compile(r"^[ ▁\t]+")

#: [_BEG_], [_TT_225] 같은 whisper 특수 토큰.
_SPECIAL_BRACKET = re.compile(r"^\[_[A-Za-z0-9]+(_[A-Za-z0-9]+)*_?\]$")
#: <|endoftext|> 류.
_SPECIAL_ANGLE = re.compile(r"^<\|.*\|>$")

#: 길이 0 으로 온 토막에 줄 최소 노출 시간 (초).
MIN_DURATION = 0.001

#: 시작 시각이 겹칠 때 밀어 넣는 최소 간격 (초).
_EPSILON = 1e-6


def _is_special(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return bool(_SPECIAL_BRACKET.match(stripped) or _SPECIAL_ANGLE.match(stripped))


def _is_pure_punctuation(text: str) -> bool:
    """구두점·기호만으로 이루어졌는가. 유니코드 카테고리로 본다."""
    if not text:
        return False
    return all(unicodedata.category(ch).startswith(("P", "S")) for ch in text)


def _split_leading(text: str) -> tuple[bool, str]:
    match = _LEADING_SPACE.match(text)
    if match:
        return True, text[match.end():]
    return False, text


def merge_eojeol(words: list[Word], min_duration: float = MIN_DURATION) -> list[Word]:
    """whisper 토막 목록을 어절 단위로 합친다.

    입력의 `text` 는 **선행 공백이 보존된 원문**이어야 한다. 미리 strip 하면
    어절 경계 신호가 사라져 아무것도 합쳐지지 않는다.
    """
    # 안전장치: 선행 공백이 **하나도** 없으면 그 백엔드는 공백을 떼고 준다는 뜻이다.
    # 그대로 합치면 문장 전체가 한 어절로 뭉쳐 자막이 붙어버린다.
    # 그럴 땐 합치지 않고 토막을 그대로 어절로 본다.
    if words and not any(_LEADING_SPACE.match(w.text) for w in words):
        return _apply_invariants(
            [{"text": w.text, "start": w.start, "end": w.end} for w in words],
            min_duration,
        )

    merged: list[dict] = []
    pending_punct = ""
    pending_start: float | None = None
    # 버퍼에 담긴 여는 부호가 선행 공백을 가지고 있었는가. 그 공백이 곧
    # 다음 어절의 시작 신호이므로, 부호가 공백을 소비했어도 신호를 이어줘야 한다.
    pending_lead = False

    for word in words:
        if _is_special(word.text):
            continue
        has_lead, core = _split_leading(word.text)
        if not core:
            continue

        if _is_pure_punctuation(core):
            if has_lead:
                # 여는 부호. 다음 어절 앞에 붙이고, 공백 신호를 물려준다.
                pending_punct += core
                pending_lead = True
                if pending_start is None:
                    pending_start = word.start
            elif merged:
                # 닫는 부호. 앞 어절이 흡수한다.
                merged[-1]["text"] += core
                merged[-1]["end"] = max(merged[-1]["end"], word.end)
            else:
                pending_punct += core
                if pending_start is None:
                    pending_start = word.start
            continue

        if has_lead or pending_lead or not merged:
            start = word.start if pending_start is None else min(pending_start, word.start)
            merged.append({
                "text": pending_punct + core,
                "start": start,
                "end": word.end,
            })
        else:
            # 선행 공백 없음 → 앞 어절의 뒷부분.
            merged[-1]["text"] += pending_punct + core
            merged[-1]["end"] = max(merged[-1]["end"], word.end)
        pending_punct = ""
        pending_start = None
        pending_lead = False

    # 끝에 남은 구두점은 버리지 않고 마지막 어절에 붙인다.
    if pending_punct and merged:
        merged[-1]["text"] += pending_punct

    return _apply_invariants(merged, min_duration)


def _apply_invariants(entries: list[dict], min_duration: float) -> list[Word]:
    """시작 단조 증가, end > start, 빈 텍스트 제거."""
    out: list[Word] = []
    previous_start = float("-inf")
    for entry in entries:
        text = entry["text"].strip()
        if not text:
            continue
        start = entry["start"]
        if start <= previous_start:
            start = previous_start + _EPSILON
        end = entry["end"]
        if end <= start:
            end = start + min_duration
        out.append(Word(text, start, end))
        previous_start = start
    return out
