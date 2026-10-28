"""API 키·토큰처럼 **남에게 보이면 안 되는 값**을 한 곳에 둔다.

작업 저장소 안의 `credentials.json` 하나에 모으고 권한을 0600 으로 좁힌다.
값을 늘릴 때마다 저장 방식을 새로 만들면, 어딘가 하나는 권한을 안 좁히게
된다. 그래서 통로를 하나로 둔다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

NAME = "credentials.json"

#: 환경변수로 덮어쓸 수 있는 값. 환경변수가 늘 이긴다 — 파일에 남기고
#: 싶지 않은 사람이 있다.
FROM_ENV = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "google_client_id": "GOOGLE_CLIENT_ID",
    "google_client_secret": "GOOGLE_CLIENT_SECRET",
}


def path_for(workspace) -> Path:
    return Path(workspace.home) / NAME


def load_all(workspace) -> dict:
    path = path_for(workspace)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def get(workspace, name: str, default: str = "") -> str:
    env = FROM_ENV.get(name)
    if env:
        from_env = os.environ.get(env, "").strip()
        if from_env:
            return from_env
    value = load_all(workspace).get(name, default)
    return str(value).strip() if value is not None else default


def put(workspace, **values: str) -> Path:
    """값을 저장한다. 주인만 읽을 수 있게 권한을 좁힌다."""
    path = path_for(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = load_all(workspace)
    raw.update({k: ("" if v is None else str(v).strip()) for k, v in values.items()})
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # 권한을 못 좁혀도 저장은 됐다.
    return path


def masked(value: str) -> str:
    """화면에 보여줄 형태. 저장한 값을 통째로 다시 내보내지 않는다."""
    value = (value or "").strip()
    if not value:
        return ""
    return f"{value[:7]}…{value[-4:]}" if len(value) > 14 else "…"
