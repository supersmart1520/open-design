"""구글 드라이브로 보내기.

실제로 올리지 않는다. 키도 인터넷도 없는 곳에서 돌아야 한다. 대신 **끊겼을
때 이어 보내는지**, **권한이 모자랄 때 뭐라고 말하는지**를 본다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from capcut_agent import drive, storage


class FakeResponse:
    def __init__(self, body: bytes = b"{}", headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, body: bytes = b"nope"):
    return urllib.error.HTTPError("u", code, "msg", {}, None)


# --- 폴더 주소 ----------------------------------------------------------------


def test_folder_id_from_a_pasted_link():
    link = "https://drive.google.com/drive/folders/1rekLC7YTxcddJ6j?hl=ko"
    assert drive.folder_id_from(link) == "1rekLC7YTxcddJ6j"


def test_folder_id_passes_a_bare_id_through():
    assert drive.folder_id_from("1rekLC7YTxcddJ6j") == "1rekLC7YTxcddJ6j"


def test_folder_id_rejects_nonsense():
    assert drive.folder_id_from("안녕") == ""
    assert drive.folder_id_from("") == ""


# --- 권한 ---------------------------------------------------------------------


def test_default_scope_is_the_narrow_one():
    # 기본이 전체 권한이면 사고로 남의 문서를 건드릴 수 있다.
    url = drive.authorize_url("아이디", "http://127.0.0.1:1/", full=False)
    assert "drive.file" in url
    assert drive.SCOPE_FULL not in url.replace("drive.file", "")


def test_full_scope_is_opt_in():
    url = drive.authorize_url("아이디", "http://127.0.0.1:1/", full=True)
    assert "auth%2Fdrive&" in url or "auth%2Fdrive&" in url


def test_refresh_token_is_requested():
    # 이게 없으면 창을 닫는 순간 다시 연결해야 한다.
    url = drive.authorize_url("아이디", "http://127.0.0.1:1/")
    assert "access_type=offline" in url


# --- 올리기 -------------------------------------------------------------------


def _fake_upload(monkeypatch, chunks_seen: list, final: dict, total_parts: int):
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if url.startswith(drive.UPLOAD_URL):
            return FakeResponse(headers={"Location": "https://세션"})
        if url == "https://세션":
            chunks_seen.append(request.data)
            calls["n"] += 1
            if calls["n"] < total_parts:
                raise _http_error(308)      # "여기까지 받았다"
            return FakeResponse(json.dumps(final).encode())
        raise AssertionError(f"예상 밖 호출: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_a_big_file_is_sent_in_pieces(tmp_path, monkeypatch):
    video = tmp_path / "숏폼.mp4"
    video.write_bytes(b"0123456789")

    seen: list = []
    _fake_upload(monkeypatch, seen, {"id": "파일", "webViewLink": "링크"}, 3)

    progress: list = []
    result = drive.upload(video, "토큰", "폴더",
                          on_progress=lambda s, t: progress.append((s, t)), chunk=4)

    assert b"".join(seen) == b"0123456789", "보낸 내용이 원본과 다릅니다"
    assert result["webViewLink"] == "링크"
    assert progress[-1] == (10, 10)


def test_308_is_not_an_error(tmp_path, monkeypatch):
    # 308 은 "계속 보내라" 는 뜻이다. 오류로 보면 멀쩡한 전송이 중단된다.
    video = tmp_path / "v.mp4"
    video.write_bytes(b"abcd")
    seen: list = []
    _fake_upload(monkeypatch, seen, {"id": "x"}, 2)
    assert drive.upload(video, "토큰", "폴더", chunk=2)["id"] == "x"


def test_missing_file_says_so(tmp_path):
    with pytest.raises(drive.DriveError, match="올릴 파일이 없습니다"):
        drive.upload(tmp_path / "없다.mp4", "토큰")


def test_unreachable_folder_explains_the_permission(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda request, timeout=None: (_ for _ in ()).throw(_http_error(404)),
    )
    with pytest.raises(drive.DriveError, match="앱이 만든 폴더가 아니면"):
        drive.upload(video, "토큰", "남의폴더")


# --- 설정 ---------------------------------------------------------------------


def test_settings_never_return_the_token(tmp_path, monkeypatch):
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    workspace = storage.resolve_workspace(tmp_path / "ws")
    from capcut_agent import secrets

    secrets.put(workspace,
                google_client_id="아이디-1234567890abcdef",
                google_refresh_token="비밀토큰입니다")

    shown = drive.settings(workspace)
    assert "비밀토큰입니다" not in json.dumps(shown, ensure_ascii=False)
    assert shown["connected"] is True


def test_sending_without_a_connection_says_so(tmp_path, monkeypatch):
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    workspace = storage.resolve_workspace(tmp_path / "ws")
    with pytest.raises(drive.DriveError, match="연결되지 않았습니다"):
        drive.send(workspace, tmp_path / "v.mp4")
