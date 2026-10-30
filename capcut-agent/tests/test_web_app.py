"""대시보드 API — 워커는 띄우지 않고 라우트만 본다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from capcut_agent.web.app import create_app
from capcut_agent.web.worker import build_command, parse_result, summarize


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    # 워커를 멈춰 둔다. 라우트만 검증하고 실제 편집은 돌리지 않는다.
    monkeypatch.setattr("capcut_agent.web.worker.Worker.start", lambda self: None)
    app = create_app(tmp_path / "ws")
    with TestClient(app) as test_client:
        test_client.workspace = app.state.workspace
        test_client.store = app.state.store
        yield test_client


def _seed(client, name: str = "강의.mp4") -> Path:
    video = client.workspace.inbox / name
    video.write_bytes(b"fake")
    return video


def test_the_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "캡컷 에이전트" in response.text


def test_status_reports_the_workspace_and_counts(client):
    body = client.get("/api/status").json()
    assert body["workspace"]["inbox"] == str(client.workspace.inbox)
    assert set(body["counts"]) >= {"queued", "review", "approved"}
    assert body["asr"]


def test_scanning_an_empty_inbox_adds_nothing(client):
    body = client.post("/api/scan", json={}).json()
    assert body == {"found": 0, "added": 0, "jobs": []}


def test_scanning_registers_inbox_videos(client):
    _seed(client)
    body = client.post("/api/scan", json={}).json()
    assert body["found"] == 1 and body["added"] == 1
    assert body["jobs"][0]["status"] == "queued"


def test_scanning_twice_does_not_duplicate(client):
    _seed(client)
    client.post("/api/scan", json={})
    body = client.post("/api/scan", json={}).json()
    assert body["added"] == 0
    assert len(client.get("/api/jobs").json()["jobs"]) == 1


def test_scan_options_are_stored_on_the_job(client):
    _seed(client)
    client.post("/api/scan", json={"options": {"motion": "fade", "shorts": True}})
    job = client.get("/api/jobs").json()["jobs"][0]
    assert job["options"]["motion"] == "fade"
    assert job["options"]["shorts"] is True


def test_a_missing_job_is_a_404(client):
    assert client.get("/api/jobs/없는거").status_code == 404
    assert client.post("/api/jobs/없는거/approve").status_code == 404
    assert client.delete("/api/jobs/없는거").status_code == 404


def test_approving_a_reviewed_job_works(client):
    _seed(client)
    client.post("/api/scan", json={})
    job_id = client.get("/api/jobs").json()["jobs"][0]["id"]
    client.store.transition(job_id, "running")
    client.store.transition(job_id, "review")
    body = client.post(f"/api/jobs/{job_id}/approve").json()
    assert body["status"] == "approved"


def test_an_illegal_transition_is_a_conflict_not_a_crash(client):
    """승인된 작업을 조용히 되돌리면 잘못된 버전이 올라간다."""
    _seed(client)
    client.post("/api/scan", json={})
    job_id = client.get("/api/jobs").json()["jobs"][0]["id"]
    response = client.post(f"/api/jobs/{job_id}/approve")
    assert response.status_code == 409
    assert "허용되지 않" in response.json()["detail"]


def test_deleting_a_job_removes_it(client):
    _seed(client)
    client.post("/api/scan", json={})
    job_id = client.get("/api/jobs").json()["jobs"][0]["id"]
    assert client.delete(f"/api/jobs/{job_id}").json() == {"ok": True}
    assert client.get("/api/jobs").json()["jobs"] == []


def test_check_returns_the_same_report_as_the_cli(client):
    assert "트랙" in client.get("/api/check").json()["text"]


# ------------------------------------------------------------- 워커 조립


class _Job:
    def __init__(self, options):
        self.source = "/videos/a.mp4"
        self.options = options


def test_the_command_defaults_to_cutting_and_srt():
    command = build_command(_Job({}), Path("/ws"))
    assert "--cut" in command and "--srt" in command and "--json" in command


def test_options_become_flags():
    command = build_command(
        _Job({"motion": "fade", "shorts": True, "fontSize": 15}), Path("/ws")
    )
    assert command[command.index("--motion") + 1] == "fade"
    assert "--shorts" in command
    assert command[command.index("--font-size") + 1] == "15"


def test_turning_cutting_off_drops_the_flag():
    assert "--cut" not in build_command(_Job({"cut": False}), Path("/ws"))


def test_empty_options_are_not_passed_as_blank_flags():
    command = build_command(_Job({"title": "", "mediaMode": None}), Path("/ws"))
    assert "--title" not in command and "--media-mode" not in command


def test_the_result_line_is_picked_out_of_noisy_output():
    report = {"드래프트": "/tmp/d"}
    lines = ["입력 : a.mp4", "__CAPCUT_JSON__ " + json.dumps(report), "검증 안내"]
    assert parse_result(lines) == report


def test_output_without_a_result_line_is_none():
    assert parse_result(["아무 말", "또 아무 말"]) is None


def test_a_broken_result_line_is_none_not_a_crash():
    assert parse_result(["__CAPCUT_JSON__ {이건 JSON 이 아님"]) is None


def test_the_summary_flattens_the_korean_report():
    summary = summarize({
        "드래프트": "/tmp/d", "캔버스": "1080x1920",
        "원본": {"길이초": 20.0, "해상도": "1080x1920", "회전": 90},
        "자막": {"장수": 3, "모션": "pop", "제목": "제목"},
        "컷": {"적용": True, "보존조각": 3, "출력길이초": 15.2},
    })
    assert summary["cues"] == 3
    assert summary["cutPieces"] == 3
    assert summary["rotation"] == 90
    assert summary["motion"] == "pop"


def test_the_summary_survives_a_sparse_report():
    assert summarize({})["draft"] is None


# ----------------------------------------- 숏폼 개수 / 처리 시간


def test_the_shorts_count_defaults_to_whole_video(client):
    _seed(client)
    client.post("/api/scan", json={})
    assert client.get("/api/jobs").json()["jobs"][0]["options"]["shortsCount"] == 0


def test_the_shorts_count_is_stored_on_the_job(client):
    _seed(client)
    client.post("/api/scan", json={"options": {"shortsCount": 5}})
    assert client.get("/api/jobs").json()["jobs"][0]["options"]["shortsCount"] == 5


def test_a_count_of_zero_captions_the_whole_video():
    command = build_command(_Job({"shortsCount": 0}), Path("/ws"))
    assert "caption" in command and "shorts" not in command


def test_a_positive_count_switches_to_pulling_shorts():
    command = build_command(_Job({"shortsCount": 5}), Path("/ws"))
    assert "shorts" in command
    assert command[command.index("--auto") + 1] == "5"


def test_the_shorts_path_still_carries_the_shared_options():
    command = build_command(
        _Job({"shortsCount": 3, "motion": "fade", "fontSize": 15}), Path("/ws")
    )
    assert command[command.index("--motion") + 1] == "fade"
    assert command[command.index("--font-size") + 1] == "15"


def test_captioning_flags_do_not_leak_into_the_shorts_path():
    """--cut 과 --title 은 shorts 서브커맨드에 없다. 넘기면 파서가 죽는다."""
    command = build_command(
        _Job({"shortsCount": 2, "cut": True, "title": "제목", "cutBy": "hybrid"}),
        Path("/ws"),
    )
    for flag in ("--cut", "--title", "--cut-by", "--media-mode"):
        assert flag not in command


# --- 폴더 열기 ---------------------------------------------------------------
#
# 영상을 넣을 폴더를 버튼으로 연다. 경로가 아니라 **이름**을 받는다는 점이
# 핵심이다 — 임의 경로를 여는 구멍이 되면 안 된다.


def test_reveal_opens_the_inbox(client, monkeypatch):
    opened: list[Path] = []
    monkeypatch.setattr("capcut_agent.web.app.reveal", lambda path: opened.append(path))

    response = client.post("/api/reveal/inbox")

    assert response.status_code == 200
    assert opened == [client.workspace.inbox]
    assert response.json()["opened"] == str(client.workspace.inbox)


def test_reveal_knows_only_its_own_folders(client, monkeypatch):
    monkeypatch.setattr(
        "capcut_agent.web.app.reveal",
        lambda path: pytest.fail(f"열려서는 안 되는 경로: {path}"),
    )

    for name in ("..", "etc", "/etc/passwd", "home/../../etc"):
        assert client.post(f"/api/reveal/{name}").status_code == 404


def test_reveal_reports_failure_instead_of_pretending(client, monkeypatch):
    def refuse(path):
        raise RuntimeError("파일 관리자가 없습니다")

    monkeypatch.setattr("capcut_agent.web.app.reveal", refuse)

    response = client.post("/api/reveal/inbox")

    assert response.status_code == 500
    assert "파일 관리자가 없습니다" in response.json()["detail"]


# --- API 키 -------------------------------------------------------------------
#
# 저장한 키를 다시 내보내면, 대시보드를 여는 누구나 키를 가져갈 수 있다.


def test_key_is_saved_and_only_shown_masked(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"

    saved = client.post("/api/settings/key", json={"key": key})
    assert saved.status_code == 200
    assert key not in saved.text

    from capcut_agent.llm import load_key
    assert load_key(client.workspace) == key

    shown = client.get("/api/status").json()["apiKey"]
    assert key not in shown
    assert shown.startswith("sk-ant-")


def test_status_says_when_there_is_no_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.get("/api/status").json()["apiKey"] == ""


def test_key_can_be_cleared(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client.post("/api/settings/key", json={"key": "sk-ant-xxxxxxxxxxxxxxx"})
    assert client.post("/api/settings/key", json={"key": ""}).json()["apiKey"] == ""


# --- 드라이브 -----------------------------------------------------------------


def _no_google_env(monkeypatch):
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)


def test_blank_secret_does_not_wipe_the_connection(client, monkeypatch):
    """폴더만 바꾸려다 연결이 끊기면 안 된다.

    화면에는 가린 값만 보이므로, 저장을 누르면 빈 비밀값이 올라온다.
    그걸 그대로 쓰면 다음 업로드가 조용히 실패한다.
    """
    _no_google_env(monkeypatch)
    from capcut_agent import secrets

    client.post("/api/drive/settings", json={
        "clientId": "아이디.apps.googleusercontent.com",
        "clientSecret": "GOCSPX-비밀",
        "folder": "https://drive.google.com/drive/folders/AAA111bbb2",
    })
    assert secrets.get(client.workspace, "google_client_secret") == "GOCSPX-비밀"

    client.post("/api/drive/settings", json={"folder": "BBB222ccc3"})

    assert secrets.get(client.workspace, "google_client_secret") == "GOCSPX-비밀"
    assert secrets.get(client.workspace, "drive_folder_id") == "BBB222ccc3"


def test_folder_link_is_reduced_to_an_id(client, monkeypatch):
    _no_google_env(monkeypatch)
    from capcut_agent import secrets

    client.post("/api/drive/settings", json={
        "clientId": "x", "folder": "https://drive.google.com/drive/folders/ZZZ999yyy8?hl=ko",
    })
    assert secrets.get(client.workspace, "drive_folder_id") == "ZZZ999yyy8"


def test_connect_needs_a_client_id_first(client, monkeypatch):
    _no_google_env(monkeypatch)
    assert client.get("/api/drive/connect").status_code == 400


def test_connect_returns_to_this_dashboard(client, monkeypatch):
    _no_google_env(monkeypatch)
    client.post("/api/drive/settings", json={"clientId": "아이디", "folder": ""})

    url = client.get("/api/drive/connect").json()["url"]

    assert "accounts.google.com" in url
    assert "api%2Fdrive%2Fcallback" in url or "api/drive/callback" in url


def test_send_moves_the_job_and_returns_the_link(client, monkeypatch):
    from capcut_agent.web import app as web_app

    video = _seed(client)
    job = client.store.add(video)
    client.store.transition(job.id, "running")
    client.store.transition(job.id, "review")
    client.store.transition(job.id, "approved")

    monkeypatch.setattr(web_app.drive_api, "send",
                        lambda ws, path, on_progress=None: {
                            "webViewLink": "https://drive.google.com/보기", "id": "F1"})

    result = client.post(f"/api/jobs/{job.id}/send")

    assert result.status_code == 200
    assert result.json()["link"].endswith("보기")
    assert client.store.get(job.id).status == "uploaded"


def test_a_failed_send_does_not_sit_in_uploading(client, monkeypatch):
    """실패를 '전송 중' 으로 남기면 끝난 줄 알고 계속 기다리게 된다."""
    from capcut_agent.web import app as web_app

    video = _seed(client, "다른영상.mp4")
    job = client.store.add(video)
    for step in ("running", "review", "approved"):
        client.store.transition(job.id, step)

    def refuse(ws, path, on_progress=None):
        raise web_app.drive_api.DriveError("드라이브가 거절했습니다")

    monkeypatch.setattr(web_app.drive_api, "send", refuse)

    assert client.post(f"/api/jobs/{job.id}/send").status_code == 500
    assert client.store.get(job.id).status == "failed"
    assert "거절" in client.store.get(job.id).error


# --- 어느 코드가 돌고 있나 -------------------------------------------------------
#
# "기능이 안 된다" 는 말의 절반은 옛 코드가 도는 것이다. 고쳐 올려도 받지
# 않았거나 앱을 다시 띄우지 않으면 겉모습이 똑같다.


def test_the_page_is_never_cached(client):
    # 브라우저가 옛 화면을 그대로 쓰면 받았는지 알 길이 없다.
    assert client.get("/").headers["cache-control"] == "no-store"


def test_status_says_which_build_is_running(client):
    build = client.get("/api/status").json()["build"]
    assert build.startswith("v")
