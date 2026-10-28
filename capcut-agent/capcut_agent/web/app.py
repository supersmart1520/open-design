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
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import storage
from ..env_check import format_report, run_checks
from .events import EventBroker, sse
from .jobs import STATUS_LABELS, JobStore, TransitionError
from .worker import Worker

STATIC = Path(__file__).parent / "static"


class JobOptions(BaseModel):
    cut: bool = True
    shorts: bool = False
    srt: bool = True
    noTitle: bool = False
    motion: str = "pop"
    fontSize: float = 12.0
    speechY: float = -850.0
    titleY: float = 1400.0
    title: str = ""
    anchor: str = "center"
    cutBy: str = "speech"
    mediaMode: str = ""
    language: str = "ko"


class ScanRequest(BaseModel):
    options: JobOptions = JobOptions()


def create_app(workspace_path: str | Path | None = None,
               draft_dir: str | None = None) -> FastAPI:
    workspace = storage.resolve_workspace(workspace_path)
    store = JobStore(workspace.home / "jobs.sqlite")
    broker = EventBroker()
    worker = Worker(store, broker, workspace.home, draft_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker.start()
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(title="캡컷 에이전트", docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    app.state.workspace = workspace
    app.state.store = store
    app.state.broker = broker
    app.state.worker = worker

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
            "counts": store.counts(),
            "labels": STATUS_LABELS,
            "workerRunning": worker.running,
        }

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
