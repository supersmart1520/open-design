"""로컬 대시보드 서버.

    python3 -m capcut_agent.cli serve

inbox 를 지켜보다 새 영상이 들어오면 작업으로 등록하고, 워커가 하나씩
편집한 뒤 **검수 대기** 로 올린다. 거기서 멈춘다 — 캡컷에서 재생해 보는 건
사람 몫이다.

바깥으로 열지 않는다. 기본 바인딩은 127.0.0.1 이다.
"""

from __future__ import annotations

import asyncio
import json
import queue
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from .. import storage
from ..env_check import format_report, run_checks
from ..llm import load_key, masked, save_key
from .. import drive as drive_api
from .. import secrets as secret_store
from ..reveal import reveal
from .events import EventBroker, sse
from .jobs import MANUAL_GATES, STATUS_LABELS, JobStore, TransitionError
from .watcher import InboxWatcher
from .worker import Worker

STATIC = Path(__file__).parent / "static"


class JobOptions(BaseModel):
    cut: bool = True
    shorts: bool = False
    srt: bool = True
    noTitle: bool = False
    motion: str = "none"
    fontSize: float = 12.0
    speechY: float = -850.0
    titleY: float = 1400.0
    title: str = ""
    anchor: str = "center"
    cutBy: str = "hybrid"
    mediaMode: str = ""
    language: str = "ko"
    #: 한 영상에서 뽑을 숏폼 개수. 0 이면 통째로 자막만 단다.
    shortsCount: int = 0


class DriveIn(BaseModel):
    """드라이브 연결에 필요한 값. 폴더는 주소를 통째로 붙여도 된다."""

    clientId: str = ""
    clientSecret: str = ""
    folder: str = ""
    full: bool = False


class KeyIn(BaseModel):
    """클로드 API 키 한 줄. 빈 문자열이면 지운다는 뜻."""

    key: str = ""


class ScanRequest(BaseModel):
    options: JobOptions = JobOptions()


def create_app(workspace_path: str | Path | None = None,
               draft_dir: str | None = None) -> FastAPI:
    workspace = storage.resolve_workspace(workspace_path)
    store = JobStore(workspace.home / "jobs.sqlite")
    broker = EventBroker()
    worker = Worker(store, broker, workspace.home, draft_dir)

    def _register(paths) -> None:
        """감시기가 찾은 파일을 작업으로 올린다."""
        for job in store.add_many(paths, JobOptions().model_dump()):
            broker.publish("job", {"job": job.to_dict()})

    watcher = InboxWatcher(workspace, _register)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker.start()
        watcher.start()
        try:
            yield
        finally:
            watcher.stop()
            worker.stop()

    app = FastAPI(title="캡컷 에이전트", docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    app.state.workspace = workspace
    app.state.store = store
    app.state.broker = broker
    app.state.worker = worker
    app.state.watcher = watcher

    # ----------------------------------------------------------- 페이지

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    # ------------------------------------------------------------- API

    @app.get("/api/status")
    def status() -> dict:
        report = run_checks(workspace)
        return {
            "ok": report.ok,
            "track": report.track,
            "trackLabel": report.track_label,
            "asr": report.asr_backend,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail, "fix": c.fix}
                for c in report.checks
            ],
            "workspace": {
                "home": str(workspace.home),
                "inbox": str(workspace.inbox),
                "scripts": str(workspace.scripts),
            },
            "apiKey": masked(load_key(workspace)),
            "drive": drive_api.settings(workspace),
            "counts": store.counts(),
            "labels": STATUS_LABELS,
            "manualGates": sorted(MANUAL_GATES),
            "workerRunning": worker.running,
            "watcherRunning": watcher.running,
        }

    @app.post("/api/settings/key")
    def set_key(body: KeyIn) -> dict:
        """클로드 API 키. 저장소 안에 주인만 읽을 수 있게 둔다.

        가려진 형태만 돌려준다. 저장한 키를 다시 내보내지 않는다.
        """
        save_key(workspace, body.key)
        return {"apiKey": masked(load_key(workspace))}

    @app.post("/api/drive/settings")
    def drive_settings(body: DriveIn) -> dict:
        """드라이브 설정 저장. 비밀값은 비워 보내면 **그대로 둔다**.

        화면에는 가린 형태만 보이므로, 사용자가 폴더만 바꾸려고 저장을
        누르면 빈 비밀값이 올라온다. 그걸 그대로 쓰면 연결이 끊긴다.
        """
        values = {}
        if body.clientId.strip():
            values["google_client_id"] = body.clientId
        if body.clientSecret.strip():
            values["google_client_secret"] = body.clientSecret
        values["drive_folder_id"] = drive_api.folder_id_from(body.folder)
        values["drive_full_scope"] = "1" if body.full else ""
        secret_store.put(workspace, **values)
        return drive_api.settings(workspace)

    @app.get("/api/drive/connect")
    def drive_connect(request: Request) -> dict:
        """구글 허락 페이지 주소. 돌아올 곳은 **이 대시보드** 자신이다."""
        client_id = secret_store.get(workspace, "google_client_id")
        if not client_id:
            raise HTTPException(400, "클라이언트 ID 를 먼저 저장하세요")
        redirect = str(request.base_url).rstrip("/") + "/api/drive/callback"
        full = secret_store.get(workspace, "drive_full_scope") == "1"
        return {"url": drive_api.authorize_url(client_id, redirect, full)}

    @app.get("/api/drive/callback")
    def drive_callback(request: Request, code: str = "", error: str = ""):
        """구글이 보내 주는 자리. 받은 코드를 토큰으로 바꿔 저장한다."""
        if error or not code:
            return HTMLResponse(f"<h3>연결하지 못했습니다</h3><p>{error or '코드가 없습니다'}</p>")
        redirect = str(request.base_url).rstrip("/") + "/api/drive/callback"
        try:
            token = drive_api.exchange_code(
                secret_store.get(workspace, "google_client_id"),
                secret_store.get(workspace, "google_client_secret"),
                code, redirect,
            )
        except drive_api.DriveError as exc:
            return HTMLResponse(f"<h3>연결하지 못했습니다</h3><p>{exc}</p>")
        secret_store.put(workspace, google_refresh_token=token)
        return HTMLResponse(
            "<h3>드라이브가 연결됐습니다</h3><p>이 창을 닫고 대시보드로 돌아가세요.</p>"
        )

    @app.post("/api/jobs/{job_id}/send")
    def send_to_drive(job_id: str) -> dict:
        """검수 완료된 작업의 결과를 드라이브로 보낸다."""
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "그런 작업이 없습니다")

        summary = job.result.get("summary") or {}
        sent = summary.get("preview") or job.source
        _transition(job_id, "uploading", "드라이브로 보내는 중")
        try:
            result = drive_api.send(workspace, Path(sent))
        except drive_api.DriveError as exc:
            # 실패를 '전송 중' 으로 남겨 두면 끝난 줄 알고 기다리게 된다.
            try:
                store.transition(job_id, "failed", error=str(exc))
            except TransitionError:
                pass
            broker.publish("job", {"job": store.get(job_id).to_dict()})
            raise HTTPException(500, str(exc))

        link = result.get("webViewLink", "")
        _transition(job_id, "uploaded", f"드라이브로 보냄 {link}".strip())
        return {"link": link, "id": result.get("id", "")}

    @app.post("/api/reveal/{name}")
    def reveal_folder(name: str) -> dict:
        """작업 폴더를 파일 관리자로 연다.

        경로가 아니라 **이름**을 받는다. 어떤 경로인지는 서버가 정한다 —
        임의 경로를 여는 구멍을 만들지 않기 위해서다.
        """
        folders = {
            "home": workspace.home,
            "inbox": workspace.inbox,
            "scripts": workspace.scripts,
        }
        target = folders.get(name)
        if target is None:
            raise HTTPException(404, "그런 폴더가 없습니다")
        try:
            reveal(target)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise HTTPException(500, f"폴더를 열지 못했습니다: {exc}")
        return {"opened": str(target)}

    @app.get("/api/jobs")
    def list_jobs(status: str | None = None) -> dict:
        return {"jobs": [job.to_dict() for job in store.list(status)]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "그런 작업이 없습니다")
        payload = job.to_dict()
        script = (job.result.get("summary") or {}).get("script")
        if script and Path(script).is_file():
            payload["scriptText"] = Path(script).read_text(encoding="utf-8")
        return payload

    @app.post("/api/scan")
    def scan(request: ScanRequest) -> dict:
        """inbox 를 훑어 새 영상을 작업으로 등록한다. 이미 있는 건 건너뛴다."""
        found = storage.list_inbox(workspace)
        before = {job.source for job in store.list()}
        jobs = store.add_many(found, request.options.model_dump())
        added = [job for job in jobs if job.source not in before]
        for job in added:
            broker.publish("job", {"job": job.to_dict()})
        return {
            "found": len(found),
            "added": len(added),
            "jobs": [job.to_dict() for job in jobs],
        }

    @app.post("/api/jobs/{job_id}/approve")
    def approve(job_id: str) -> dict:
        return _transition(job_id, "approved", "검수 완료")

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str) -> dict:
        return _transition(job_id, "queued", "다시 대기열로")

    @app.post("/api/jobs/{job_id}/reopen")
    def reopen(job_id: str) -> dict:
        """승인 뒤에 고칠 게 보이면 재검수로 되돌린다.

        승인된 채로 조용히 고치면 어느 버전이 올라갔는지 알 수 없게 된다.
        """
        return _transition(job_id, "review", "재검수")

    @app.delete("/api/jobs/{job_id}")
    def remove(job_id: str) -> dict:
        if not store.delete(job_id):
            raise HTTPException(404, "그런 작업이 없습니다")
        broker.publish("removed", {"id": job_id})
        return {"ok": True}

    def _transition(job_id: str, status: str, message: str) -> dict:
        try:
            job = store.transition(job_id, status, message=message)
        except KeyError:
            raise HTTPException(404, "그런 작업이 없습니다")
        except TransitionError as exc:
            # 검수 완료된 작업이 조용히 되돌아가면 잘못된 버전이 올라간다.
            raise HTTPException(409, str(exc))
        broker.publish("job", {"job": job.to_dict()})
        return job.to_dict()

    # ------------------------------------------------------------- SSE

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        channel = broker.subscribe()

        async def stream():
            try:
                yield sse(json.dumps({"kind": "hello"}, ensure_ascii=False))
                while True:
                    try:
                        message = await asyncio.get_running_loop().run_in_executor(
                            None, channel.get, True, 15
                        )
                    except queue.Empty:
                        # 프록시가 조용한 연결을 끊지 않도록 한 번씩 찔러준다.
                        yield ": keep-alive\n\n"
                        continue
                    yield sse(message)
            finally:
                broker.unsubscribe(channel)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/check")
    def check() -> dict:
        return {"text": format_report(run_checks(workspace))}

    return app
