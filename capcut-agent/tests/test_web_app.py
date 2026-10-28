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
