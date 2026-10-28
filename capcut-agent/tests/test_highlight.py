"""숏폼 후보 고르기 — 기승전결과 네 기준.

점수는 **순위용 참고치**다. 인기를 보장하는 수치가 아니다. 그래서 여기서
보는 것은 "좋은 구간을 맞혔나" 가 아니라 "기준이 적힌 대로 작동하나" 다.
"""

from __future__ import annotations

from capcut_agent.highlight import (
    Candidate,
    arc_of,
    propose,
    score_window,
)
from capcut_agent.transcribe import Segment, Transcript


def _script(lines: list[str], each: float = 12.0) -> Transcript:
    segments = [
        Segment(start=i * each, end=i * each + each, text=text)
        for i, text in enumerate(lines)
    ]
    return Transcript(
        language="ko", text=" ".join(lines), segments=segments, model="테스트"
    )


ARC_LINES = [
    "오늘은 자막 다는 법을 말씀드릴게요.",
    "그런데 왜 자막이 자꾸 밀릴까요?",
    "그래서 단어 타임스탬프를 쓰면 됩니다.",
    "정리하면 어절 경계로 끊는 게 핵심입니다.",
]


def test_arc_finds_all_four_stages():
    found = arc_of(_script(ARC_LINES).segments)
    assert list(found) == ["기", "승", "전", "결"]
    assert "왜" in found["승"]


def test_arc_respects_position():
    # 결론 신호어가 맨 앞에만 있으면 결(結)로 치지 않는다. 자리가 틀렸다.
    found = arc_of(_script(["정리하면 이렇습니다.", "음 그리고", "어 뭐"]).segments)
    assert found.get("결") != "정리하면 이렇습니다."


def test_unfinished_ending_is_not_a_conclusion():
    found = arc_of(_script(["오늘은 말이죠", "그래서 저는", "음 그"]).segments)
    assert "결" not in found


def test_complete_arc_outscores_a_cold_start():
    whole, _, arc = score_window(_script(ARC_LINES).segments)
    assert len(arc) == 4

    cold, reasons, _ = score_window(_script([
        "안녕하세요 반갑습니다.",
        "지난 시간에 이어서 하겠습니다.",
        "오늘도 잘 부탁드립니다.",
        "시작하겠습니다.",
    ]).segments)

    assert whole > cold
    assert any("앞을 들어야" in r for r in reasons)


def test_reaction_and_payoff_are_counted():
    _, reasons, _ = score_window(_script([
        "오늘은 제가 겪은 실수를 말씀드릴게요.",
        "왜 그게 문제냐면요.",
        "그래서 이런 방법으로 바꿨어요. 대박이었죠.",
        "정리하면 이 세 가지만 기억하세요.",
    ]).segments)
    assert any("감정 반응" in r for r in reasons)
    assert any("가져갈 내용" in r for r in reasons)


def test_a_complete_arc_is_preferred_even_with_a_lower_raw_window():
    # 기승전결이 있는 쪽이 먼저 나와야 한다. 혼자서도 이해되는 조건이다.
    lines = ARC_LINES + [
        "음 그러니까 어 네.",
        "뭐 그렇습니다 네.",
        "어 다음으로 넘어가서 어.",
    ]
    picked = propose(_script(lines), limit=1)
    assert len(picked) == 1
    assert len(picked[0].arc) == 4


def test_topics_do_not_repeat():
    # 같은 얘기가 두 번 뽑히면 숏폼 다섯 개가 한 얘기가 된다.
    lines = ARC_LINES * 3
    picked = propose(_script(lines), limit=5)
    bodies = [c.text for c in picked]
    assert len(bodies) == len(set(bodies)), "같은 대본이 두 번 뽑혔습니다"


def test_candidates_never_come_back_empty():
    # 기준을 못 채웠다고 빈손으로 주면 사람이 볼 재료조차 없어진다.
    picked = propose(_script(["음", "어", "네", "뭐", "그", "음 네"]), limit=3)
    assert picked, "후보가 하나도 없습니다"
    assert any("기승전결" in r for c in picked for r in c.reasons)


def test_arc_survives_the_exchange_file():
    # 사람이 고친 파일을 다시 읽어도 "왜 가져왔는지" 가 남아 있어야 한다.
    original = Candidate(
        index=1, start=0.0, end=30.0, title="제목", text="대본",
        score=9.0, reasons=["기승전결이 다 있음"], arc={"기": "ㄱ", "결": "ㄹ"},
    )
    again = Candidate.from_dict(original.to_dict())
    assert again.arc == {"기": "ㄱ", "결": "ㄹ"}
