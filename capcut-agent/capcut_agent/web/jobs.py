"""작업 저장소 — SQLite.

대시보드는 영상 하나를 '작업' 으로 다룬다. 상태는 한 방향으로만 흐른다.

    queued ──▶ running ──▶ review ──▶ approved
                  │
                  └──────▶ failed ──▶ (다시 queued)

`review` 는 **사람이 캡컷에서 확인할 차례**라는 뜻이다. 이 도구는 거기서
멈춘다. 빌드 성공은 검증이 아니기 때문이다.

로컬 파일 하나로 끝낸다. 서버가 죽어도 작업 기록과 상태가 남아야 한다.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Iterable

#: 상태 전이표. 여기 없는 전이는 거부한다.
#:
#:   등록 ──▶ 편집 중 ──▶ 검수 대기 ──▶ 검수 완료 ──▶ 전송 중 ──▶ 전송 완료 ──▶ 업로드 완료
#:               │             ▲                         │
#:               └─▶ 실패 ─────┘                         └─▶ 실패
#:
#: **전송 완료와 업로드 완료를 반드시 구분한다.** 드라이브에 올라간 것과
#: SNS 에 게시된 것은 다른 일이다. 하나로 뭉치면 올리지도 않은 영상을
#: 게시했다고 착각한다.
TRANSITIONS: dict[str, frozenset] = {
    "queued": frozenset({"running", "failed"}),
    "running": frozenset({"review", "failed"}),
    "review": frozenset({"approved", "queued", "failed"}),
    # 승인 뒤에도 고칠 일이 생기면 재검수로 되돌릴 수 있다.
    "approved": frozenset({"review", "uploading"}),
    "uploading": frozenset({"uploaded", "failed", "approved"}),
    # 전송은 끝났지만 아직 게시 전. 다시 올릴 수도 있다.
    "uploaded": frozenset({"published", "uploading", "review"}),
    # 게시 뒤 잘못을 발견하면 되돌린다.
    "published": frozenset({"uploaded"}),
    "failed": frozenset({"queued", "approved"}),
}

STATUSES = tuple(TRANSITIONS)

#: 사람이 읽는 상태 이름.
STATUS_LABELS = {
    "queued": "대기",
    "running": "편집 중",
    "review": "검수 대기",
    "approved": "검수 완료",
    "uploading": "전송 중",
    "uploaded": "전송 완료",
    "published": "업로드 완료",
    "failed": "실패",
}

#: 사람이 손을 대야 넘어가는 단계. 자동화가 여기서 멈춘다.
MANUAL_GATES = frozenset({"review", "uploaded"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL,
    stage       TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    options     TEXT NOT NULL DEFAULT '{}',
    result      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_source ON jobs(source);
"""


class TransitionError(RuntimeError):
    """허용되지 않은 상태 전이."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    source: str
    name: str
    status: str
    stage: str = ""
    message: str = ""
    error: str = ""
    options: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "name": self.name,
            "status": self.status,
            "statusLabel": self.status_label,
            "stage": self.stage,
            "message": self.message,
            "error": self.error,
            "options": self.options,
            "result": self.result,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"], source=row["source"], name=row["name"],
        status=row["status"], stage=row["stage"], message=row["message"],
        error=row["error"],
        options=json.loads(row["options"] or "{}"),
        result=json.loads(row["result"] or "{}"),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


class JobStore:
    """SQLite 작업 저장소. 스레드 간 공유해도 되도록 쓰기를 직렬화한다."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------- 읽기

    def list(self, status: str | None = None) -> list[Job]:
        query = "SELECT * FROM jobs"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            return [_row_to_job(row) for row in conn.execute(query, params)]

    def get(self, job_id: str) -> Job | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def find_by_source(self, source: Path | str) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE source = ?", (str(source),)
            ).fetchone()
        return _row_to_job(row) if row else None

    def next_queued(self) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return _row_to_job(row) if row else None

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        tally = {status: 0 for status in STATUSES}
        for row in rows:
            tally[row["status"]] = row["n"]
        return tally

    # ------------------------------------------------------------- 쓰기

    def add(self, source: Path | str, options: dict | None = None) -> Job:
        """영상 하나를 작업으로 등록한다. 같은 파일은 다시 등록하지 않는다."""
        source = Path(source)
        existing = self.find_by_source(source)
        if existing:
            return existing
        job = Job(
            id=uuid.uuid4().hex[:12],
            source=str(source),
            name=source.stem,
            status="queued",
            options=options or {},
            created_at=_now(),
            updated_at=_now(),
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, source, name, status, options, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job.id, job.source, job.name, job.status,
                 json.dumps(job.options, ensure_ascii=False),
                 job.created_at, job.updated_at),
            )
        return job

    def add_many(self, sources: Iterable[Path], options: dict | None = None) -> list[Job]:
        return [self.add(source, options) for source in sources]

    def progress(self, job_id: str, stage: str, message: str = "") -> Job | None:
        """상태는 그대로 두고 진행 단계만 갱신한다."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET stage = ?, message = ?, updated_at = ? WHERE id = ?",
                (stage, message, _now(), job_id),
            )
        return self.get(job_id)

    def transition(
        self,
        job_id: str,
        status: str,
        *,
        message: str = "",
        error: str = "",
        result: dict | None = None,
        stage: str | None = None,
    ) -> Job:
        """상태를 바꾼다. 전이표에 없는 이동은 거부한다.

        거부가 중요한 이유: 검수 완료된 작업이 조용히 다시 편집되면 잘못된
        버전이 올라간다.
        """
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if status != job.status and status not in TRANSITIONS.get(job.status, frozenset()):
            raise TransitionError(
                f"{job.status} → {status} 은 허용되지 않습니다 "
                f"(가능: {', '.join(sorted(TRANSITIONS.get(job.status, []))) or '없음'})"
            )
        merged = dict(job.result)
        if result:
            merged.update(result)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, message = ?, error = ?, result = ?,"
                " stage = ?, updated_at = ? WHERE id = ?",
                (status, message, error,
                 json.dumps(merged, ensure_ascii=False),
                 job.stage if stage is None else stage,
                 _now(), job_id),
            )
        updated = self.get(job_id)
        assert updated is not None
        return updated

    def delete(self, job_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cursor.rowcount > 0
