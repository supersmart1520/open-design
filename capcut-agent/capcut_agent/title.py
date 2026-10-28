"""제목 자막 규칙.

제목은 영상당 **한 장, 한 줄**이다. 숏폼에서 제목은 첫 1초 안에 읽혀야 하므로
규칙이 자막보다 빡빡하다.

  1. 한 줄. 줄바꿈과 연속 공백은 공백 하나로 눕힌다.
  2. 짧게. 기본 22자. 넘으면 **어절 경계에서** 자르고 말줄임표를 붙인다
     (글자 중간에서 자르면 읽다 막힌다).
  3. 끝 마침표는 뗀다. 제목에 마침표는 군더더기다. 물음표·느낌표는 남긴다 —
     후킹에 쓰인다.
  4. 감싸는 따옴표·괄호는 벗긴다. 전사가 따옴표를 물고 오는 일이 잦다.
  5. 비면 제목을 넣지 않는다. 빈 텍스트 세그먼트는 캡컷에서 빈 상자로 남는다.
"""

from __future__ import annotations

import re

#: 제목 기본 최대 글자 수.
DEFAULT_MAX_CHARS = 22

#: 제목이 이보다 짧으면 자르지 않는다 (자를 여지가 없다).
_MIN_USEFUL = 4

#: 감싸는 기호. 양쪽에서 벗긴다.
_WRAPPERS = "\"'“”‘’()[]{}«»「」『』<>"

#: 끝에서 떼는 기호. 물음표·느낌표는 후킹이라 남긴다.
_TRAILING_NOISE = ".,·…:;~-—"

_WHITESPACE = re.compile(r"\s+")


def clean_title(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """제목 규칙을 적용한다. 쓸 수 없으면 빈 문자열."""
    if not text:
        return ""

    line = _WHITESPACE.sub(" ", str(text)).strip()
    # 감싸는 기호를 벗긴다. 중첩될 수 있어 변화가 없을 때까지.
    while line and line[0] in _WRAPPERS:
        line = line[1:].strip()
    while line and line[-1] in _WRAPPERS:
        line = line[:-1].strip()
    line = line.rstrip(_TRAILING_NOISE).strip()

    if not line:
        return ""
    if len(line) <= max_chars:
        return line
    return _truncate_at_word(line, max_chars)


def _truncate_at_word(line: str, max_chars: int) -> str:
    """어절 경계에서 자른다. 글자 중간에서 자르면 읽다 막힌다."""
    if max_chars <= _MIN_USEFUL:
        return line[:max_chars]

    budget = max_chars - 1          # 말줄임표 자리
    cut = line[:budget]
    space = cut.rfind(" ")
    # 어절 경계가 너무 앞이면(절반도 못 채우면) 그냥 글자 수로 자른다.
    if space >= budget // 2:
        cut = cut[:space]
    return cut.rstrip(_TRAILING_NOISE + " ") + "…"
