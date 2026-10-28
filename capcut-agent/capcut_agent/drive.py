"""편집 결과를 구글 드라이브로 올린다.

의존성을 늘리지 않으려고 표준 라이브러리만 쓴다. 구글 클라이언트 라이브러리는
받아 오는 것이 많고, 이 앱이 드라이브에 하는 일은 **파일 하나 올리기** 뿐이다.

## 권한

기본은 `drive.file` 이다. **앱이 만든 파일만** 보고 건드릴 수 있어서, 사고로
다른 문서를 지우거나 읽을 길이 없다. 대신 이미 있는 남의 폴더에는 못 넣는다.
그래서 폴더를 지정하지 않으면 앱이 자기 폴더를 만들어 거기 넣는다.

이미 쓰던 폴더에 넣어야 하면 전체 권한(`drive`)으로 다시 연결해야 한다.
그 경우 드라이브 전체를 볼 수 있게 되므로, 기본값으로 두지 않는다.

## 올리는 방식

영상은 크다. 한 번에 밀어 넣으면 중간에 끊겼을 때 처음부터 다시다. 그래서
이어올리기(resumable)를 쓰고, 끊기면 서버에 어디까지 받았는지 물어 그 뒤부터
보낸다.
"""

from __future__ import annotations

import json
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import secrets

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FILES_URL = "https://www.googleapis.com/drive/v3/files"

#: 앱이 만든 파일만. 사고로 다른 문서를 건드릴 길이 없다.
SCOPE_FILE = "https://www.googleapis.com/auth/drive.file"
#: 이미 있는 폴더에 넣어야 할 때만. 드라이브 전체가 보인다.
SCOPE_FULL = "https://www.googleapis.com/auth/drive"

#: 앱이 자기 폴더를 만들 때 쓰는 이름.
OWN_FOLDER = "캡컷 에이전트"
FOLDER_MIME = "application/vnd.google-apps.folder"

#: 한 번에 보내는 양. 끊겼을 때 다시 보낼 최대치이기도 하다.
CHUNK = 8 * 1024 * 1024
TIMEOUT = 300.0


class DriveError(RuntimeError):
    """드라이브에 보내지 못했다. 메시지에 이유를 담는다."""


def folder_id_from(text: str) -> str:
    """폴더 주소에서 ID 만 뽑는다. ID 를 그대로 줘도 받는다.

    사람은 주소창을 통째로 복사해 붙인다. 거기서 ID 를 직접 떼어내라고
    하면 한 번은 틀린다.
    """
    text = (text or "").strip()
    if not text:
        return ""
    found = re.search(r"/folders/([A-Za-z0-9_-]+)", text)
    if found:
        return found.group(1)
    found = re.search(r"[?&]id=([A-Za-z0-9_-]+)", text)
    if found:
        return found.group(1)
    return text if re.fullmatch(r"[A-Za-z0-9_-]{10,}", text) else ""


def _post_form(url: str, fields: dict, timeout: float = 60.0) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise DriveError(f"구글이 거절했습니다 ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DriveError(f"구글에 닿지 못했습니다: {exc}") from exc


def authorize_url(client_id: str, redirect_uri: str, full: bool = False) -> str:
    """사용자가 열 주소. 여기서 허락하면 코드가 돌아온다."""
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE_FULL if full else SCOPE_FILE,
        # 창을 닫아도 계속 쓰려면 새로고침 토큰이 있어야 한다.
        "access_type": "offline",
        "prompt": "consent",
    })
    return f"{AUTH_URL}?{query}"


def exchange_code(client_id: str, client_secret: str, code: str,
                  redirect_uri: str) -> str:
    """허락 코드를 **새로고침 토큰**으로 바꾼다. 이 토큰만 저장한다."""
    payload = _post_form(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    token = payload.get("refresh_token", "")
    if not token:
        raise DriveError(
            "새로고침 토큰을 받지 못했습니다. 구글 계정의 앱 권한을 지우고 "
            "다시 연결해 보세요."
        )
    return token


def access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """잠깐 쓰는 토큰. 올릴 때마다 새로 받는다 — 저장하지 않는다."""
    payload = _post_form(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    token = payload.get("access_token", "")
    if not token:
        raise DriveError("접근 토큰을 받지 못했습니다. 다시 연결해 주세요.")
    return token


def _json_request(url: str, token: str, body: dict | None = None,
                  method: str = "GET") -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json; charset=UTF-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 404:
            raise DriveError(
                "그 폴더를 찾지 못했습니다. 앱이 만든 폴더가 아니면 "
                "기본 권한으로는 넣을 수 없습니다 — 전체 권한으로 다시 "
                "연결하거나, 폴더를 비워 두고 앱 폴더를 쓰세요."
            ) from exc
        raise DriveError(f"드라이브가 거절했습니다 ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DriveError(f"드라이브에 닿지 못했습니다: {exc}") from exc


def ensure_folder(token: str, name: str = OWN_FOLDER) -> str:
    """앱 폴더를 찾거나 만든다. 기본 권한으로도 되는 길."""
    query = urllib.parse.urlencode({
        "q": f"name='{name}' and mimeType='{FOLDER_MIME}' and trashed=false",
        "fields": "files(id,name)",
        "pageSize": "1",
    })
    found = _json_request(f"{FILES_URL}?{query}", token).get("files") or []
    if found:
        return found[0]["id"]
    made = _json_request(
        f"{FILES_URL}?fields=id", token,
        {"name": name, "mimeType": FOLDER_MIME}, method="POST",
    )
    return made["id"]


def _start_session(token: str, name: str, folder_id: str, size: int,
                   content_type: str) -> str:
    body: dict = {"name": name}
    if folder_id:
        body["parents"] = [folder_id]
    request = urllib.request.Request(
        f"{UPLOAD_URL}?uploadType=resumable&fields=id,webViewLink",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json; charset=UTF-8",
            "x-upload-content-type": content_type,
            "x-upload-content-length": str(size),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            location = response.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 404:
            raise DriveError(
                "그 폴더를 찾지 못했습니다. 앱이 만든 폴더가 아니면 "
                "기본 권한으로는 넣을 수 없습니다."
            ) from exc
        raise DriveError(f"올리기를 시작하지 못했습니다 ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise DriveError(f"드라이브에 닿지 못했습니다: {exc}") from exc

    if not location:
        raise DriveError("올릴 자리를 받지 못했습니다.")
    return location


def _send_chunk(session: str, chunk: bytes, offset: int, total: int):
    """한 덩어리를 보낸다. 아직 안 끝났으면 `None`, 끝났으면 결과."""
    last = offset + len(chunk) - 1
    request = urllib.request.Request(
        session, data=chunk, method="PUT",
        headers={
            "content-length": str(len(chunk)),
            "content-range": f"bytes {offset}-{last}/{total}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        # 308 은 "여기까지 받았다" 는 뜻이다. 오류가 아니다.
        if exc.code == 308:
            return None
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise DriveError(f"올리다 멈췄습니다 ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise DriveError(f"올리다 끊겼습니다: {exc}") from exc


def upload(path: Path, token: str, folder_id: str = "",
           on_progress=None, chunk: int = CHUNK) -> dict:
    """파일 하나를 올린다. (id, webViewLink) 를 돌려준다."""
    path = Path(path)
    if not path.is_file():
        raise DriveError(f"올릴 파일이 없습니다: {path}")

    total = path.stat().st_size
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    session = _start_session(token, path.name, folder_id, total, content_type)

    sent = 0
    with path.open("rb") as stream:
        while sent < total:
            block = stream.read(chunk)
            if not block:
                break
            result = _send_chunk(session, block, sent, total)
            sent += len(block)
            if on_progress:
                on_progress(sent, total)
            if result is not None:
                return result
    raise DriveError("다 보냈는데 끝났다는 답을 받지 못했습니다.")


def settings(workspace) -> dict:
    """저장된 드라이브 설정. 토큰은 **가린 채로만** 내보낸다."""
    client_id = secrets.get(workspace, "google_client_id")
    token = secrets.get(workspace, "google_refresh_token")
    return {
        "clientId": secrets.masked(client_id),
        "connected": bool(token),
        "folderId": secrets.get(workspace, "drive_folder_id"),
        "full": secrets.get(workspace, "drive_full_scope") == "1",
    }


def send(workspace, path: Path, on_progress=None) -> dict:
    """설정을 읽어 파일을 올린다. 폴더가 없으면 앱 폴더를 만든다."""
    client_id = secrets.get(workspace, "google_client_id")
    client_secret = secrets.get(workspace, "google_client_secret")
    refresh = secrets.get(workspace, "google_refresh_token")
    if not (client_id and client_secret and refresh):
        raise DriveError("드라이브가 아직 연결되지 않았습니다.")

    token = access_token(client_id, client_secret, refresh)
    folder_id = secrets.get(workspace, "drive_folder_id")
    if not folder_id:
        folder_id = ensure_folder(token)
        secrets.put(workspace, drive_folder_id=folder_id)
    return upload(path, token, folder_id, on_progress=on_progress)
