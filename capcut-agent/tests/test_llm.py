"""클로드에게 대본을 읽히는 길.

실제 호출은 하지 않는다. 키도 인터넷도 없는 곳에서 돌아야 하고, 테스트가
돈을 쓰면 안 된다. 대신 **응답을 어떻게 받아들이는지**를 본다 — 모델이
없는 구간을 지어내거나 형식을 어겨도 앱이 무너지지 않아야 한다.
"""

from __future__ import annotations

import json

import pytest

from capcut_agent import llm
from capcut_agent.transcribe import Segment, Transcript


def _script(count: int = 6) -> Transcript:
    lines = [f"{i}번째 문장입니다." for i in range(count)]
    segments = [
        Segment(start=i * 15.0, end=i * 15.0 + 15, text=t)
        for i, t in enumerate(lines)
    ]
    return Transcript(language="ko", text=" ".join(lines), segments=segments)


def _answer(items: list[dict]) -> str:
    return json.dumps({"후보": items}, ensure_ascii=False)


# --- 키 ----------------------------------------------------------------------


def test_key_round_trips(tmp_path, monkeypatch):
    from capcut_agent import storage

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = storage.resolve_workspace(tmp_path / "ws")

    assert llm.load_key(workspace) == ""
    path = llm.save_key(workspace, "sk-ant-test-1234567890")
    assert llm.load_key(workspace) == "sk-ant-test-1234567890"
    # 키 파일은 주인만 읽을 수 있어야 한다.
    assert path.stat().st_mode & 0o077 == 0


def test_environment_wins(tmp_path, monkeypatch):
    from capcut_agent import storage

    workspace = storage.resolve_workspace(tmp_path / "ws")
    llm.save_key(workspace, "파일키")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "환경키")
    assert llm.load_key(workspace) == "환경키"


def test_masked_never_shows_the_whole_key():
    key = "sk-ant-api03-abcdefghijklmnop"
    shown = llm.masked(key)
    assert key not in shown
    assert shown.startswith("sk-ant-")
    assert llm.masked("") == ""


# --- 대본 보내기 --------------------------------------------------------------


def test_script_is_numbered_so_times_cannot_be_invented():
    text = llm.numbered_script(_script(3))
    assert text.splitlines()[0].startswith("[0] (0.0s)")
    assert "[2]" in text


def test_transcript_is_marked_as_data():
    # 대본 안에 지시문처럼 보이는 말이 있어도 따르면 안 된다.
    assert "자료" in llm.INSTRUCTIONS
    body = llm.build_body(_script(2), 5, "모델")
    assert "=== 대본 시작 ===" in body["messages"][0]["content"]


# --- 응답 받아들이기 ----------------------------------------------------------


def test_candidates_carry_the_arc_and_reasons():
    raw = _answer([{
        "시작번호": 0, "끝번호": 2, "제목": "자막이 밀리는 이유",
        "기승전결": {"기": "ㄱ", "승": "ㄴ", "전": "ㄷ", "결": "ㄹ"},
        "주제": "자막 타이밍", "점수": 8.5, "근거": ["질문으로 시작"],
    }])
    picked = llm.to_candidates(raw, _script())

    assert len(picked) == 1
    assert picked[0].arc == {"기": "ㄱ", "승": "ㄴ", "전": "ㄷ", "결": "ㄹ"}
    assert picked[0].title == "자막이 밀리는 이유"
    assert any("주제: 자막 타이밍" in r for r in picked[0].reasons)
    assert picked[0].start == 0.0 and picked[0].end == 45.0


def test_invented_segments_are_dropped():
    # 대본에 없는 번호를 가리키면 버린다. 없는 구간은 편집할 수 없다.
    raw = _answer([
        {"시작번호": 0, "끝번호": 1, "제목": "진짜"},
        {"시작번호": 90, "끝번호": 99, "제목": "지어낸 것"},
        {"시작번호": 3, "끝번호": 1, "제목": "거꾸로"},
    ])
    picked = llm.to_candidates(raw, _script())
    assert [c.title for c in picked] == ["진짜"]


def test_shortfall_is_passed_on_not_hidden():
    raw = _answer([{"시작번호": 0, "끝번호": 1, "제목": "ㅇ",
                    "부족한점": "결론이 없습니다"}])
    picked = llm.to_candidates(raw, _script())
    assert any("결론이 없습니다" in r for r in picked[0].reasons)


def test_code_fence_is_tolerated():
    raw = "```json\n" + _answer([{"시작번호": 0, "끝번호": 1}]) + "\n```"
    assert len(llm.to_candidates(raw, _script())) == 1


def test_unreadable_answer_is_reported_not_swallowed():
    with pytest.raises(llm.LLMUnavailable, match="읽지 못했습니다"):
        llm.to_candidates("미안하지만 못 하겠습니다", _script())


def test_empty_selection_is_unavailable():
    with pytest.raises(llm.LLMUnavailable, match="쓸 수 있는 후보가 없습니다"):
        llm.to_candidates(_answer([]), _script())


def test_candidates_come_back_in_video_order():
    raw = _answer([
        {"시작번호": 4, "끝번호": 5, "제목": "뒤"},
        {"시작번호": 0, "끝번호": 1, "제목": "앞"},
    ])
    picked = llm.to_candidates(raw, _script())
    assert [c.title for c in picked] == ["앞", "뒤"]
    assert [c.index for c in picked] == [1, 2]


def test_missing_key_never_reaches_the_network(monkeypatch):
    monkeypatch.setattr(llm, "ask", lambda *a, **kw: pytest.fail("호출되면 안 됩니다"))
    with pytest.raises(llm.LLMUnavailable, match="API 키가 없습니다"):
        llm.select(_script(), 5, "")


# --- 어떻게 골랐는지 숨기지 않기 ------------------------------------------------


def _args(**kw):
    import argparse

    base = dict(local_only=False, llm_model="", limit=5,
                min_seconds=20.0, max_seconds=180.0)
    base.update(kw)
    return argparse.Namespace(**base)


def test_falls_back_to_rules_and_says_so(tmp_path, monkeypatch, capsys):
    """모델을 못 쓰면 규칙 기반으로 떨어지되, 그 사실을 감추지 않는다."""
    from capcut_agent import storage
    from capcut_agent.cli import choose_candidates

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = storage.resolve_workspace(tmp_path / "ws")
    llm.save_key(workspace, "sk-ant-xxxx")

    def refuse(*a, **kw):
        raise llm.LLMUnavailable("인터넷이 끊겼습니다")

    monkeypatch.setattr(llm, "select", refuse)

    picked, how = choose_candidates(_args(), workspace, _script(8))

    assert "규칙 기반" in how
    assert picked
    assert "인터넷이 끊겼습니다" in capsys.readouterr().err


def test_model_result_is_labelled(tmp_path, monkeypatch):
    from capcut_agent import storage
    from capcut_agent.cli import choose_candidates
    from capcut_agent.highlight import Candidate

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = storage.resolve_workspace(tmp_path / "ws")
    llm.save_key(workspace, "sk-ant-xxxx")
    monkeypatch.setattr(llm, "select", lambda *a, **kw: [
        Candidate(index=1, start=0.0, end=30.0, title="ㅇ", text="ㅇ", score=9.0)
    ])

    picked, how = choose_candidates(_args(), workspace, _script(8))

    assert len(picked) == 1
    assert "클로드가 대본을 읽고 선정" in how
    assert llm.DEFAULT_MODEL in how


def test_local_only_never_calls_the_model(tmp_path, monkeypatch):
    from capcut_agent import storage
    from capcut_agent.cli import choose_candidates

    workspace = storage.resolve_workspace(tmp_path / "ws")
    llm.save_key(workspace, "sk-ant-xxxx")
    monkeypatch.setattr(llm, "select", lambda *a, **kw: pytest.fail("불리면 안 됩니다"))

    _, how = choose_candidates(_args(local_only=True), workspace, _script(8))
    assert how.startswith("규칙 기반")


def test_arc_is_shown_with_missing_stages_visible():
    from capcut_agent.cli import format_arc

    # 어느 단계가 없는지가 보여야 한다. 빠진 걸 숨기면 판단을 못 한다.
    assert "기··결" in format_arc({"기": "ㄱ", "결": "ㄹ"})
    assert format_arc({}) == "기승전결: 찾지 못함"


# --- 게시문구 -----------------------------------------------------------------


def test_post_text_normalises_hashtags(monkeypatch):
    monkeypatch.setattr(llm, "ask", lambda *a, **kw: json.dumps({
        "제목": "자막이 밀리는 이유",
        "설명": "두 문장짜리 설명입니다.",
        "해시태그": ["영상편집", "#캡컷", " 자막 "],
    }, ensure_ascii=False))

    post = llm.post_text(_script(), "sk-ant-x")

    assert post["해시태그"] == ["#영상편집", "#캡컷", "#자막"]
    assert post["제목"] == "자막이 밀리는 이유"


def test_post_text_needs_a_key(monkeypatch):
    monkeypatch.setattr(llm, "ask", lambda *a, **kw: pytest.fail("불리면 안 됩니다"))
    with pytest.raises(llm.LLMUnavailable, match="API 키가 없습니다"):
        llm.post_text(_script(), "")


def test_formatted_post_is_ready_to_paste():
    text = llm.format_post({
        "제목": "제목", "설명": "설명입니다.", "해시태그": ["#가", "#나"],
    })
    assert text.splitlines()[0] == "제목"
    assert text.rstrip().endswith("#가 #나")


def test_transcript_is_data_in_the_post_prompt():
    assert "자료" in llm.POST_INSTRUCTIONS
    # 없는 수치를 지어내면 그대로 올라간다.
    assert "지어내지" in llm.POST_INSTRUCTIONS


def test_failure_to_write_post_text_does_not_stop_editing(tmp_path, monkeypatch, capsys):
    from capcut_agent import storage
    from capcut_agent.cli import write_post_text

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = storage.resolve_workspace(tmp_path / "ws")
    llm.save_key(workspace, "sk-ant-x")

    def refuse(*a, **kw):
        raise llm.LLMUnavailable("끊겼습니다")

    monkeypatch.setattr(llm, "post_text", refuse)

    assert write_post_text(workspace, "영상", _script()) is None
    assert "게시문구를 만들지 못했습니다" in capsys.readouterr().err
