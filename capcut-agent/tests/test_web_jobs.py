"""작업 저장소 — 상태 전이가 핵심이다.

검수 완료된 작업이 조용히 되돌아가면 잘못된 버전이 올라간다. 전이표에 없는
이동은 거부해야 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capcut_agent.web.jobs import (
    STATUS_LABELS,
    TRANSITIONS,
    JobStore,
    TransitionError,
)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite")


def test_adding_a_video_queues_it(store):
    job = store.add("/videos/a.mp4")
    assert job.status == "queued"
    assert job.name == "a"


def test_the_same_file_is_never_queued_twice(store):
    first = store.add("/videos/a.mp4")
    second = store.add("/videos/a.mp4")
    assert first.id == second.id
    assert len(store.list()) == 1


def test_listing_can_filter_by_status(store):
    store.add("/videos/a.mp4")
    job = store.add("/videos/b.mp4")
    store.transition(job.id, "running")
    assert [j.name for j in store.list("running")] == ["b"]


def test_next_queued_skips_running_jobs(store):
    first = store.add("/videos/a.mp4")
    store.add("/videos/b.mp4")
    store.transition(first.id, "running")
    assert store.next_queued().name == "b"


def test_next_queued_is_none_when_the_queue_is_empty(store):
    assert store.next_queued() is None


def test_progress_does_not_change_the_status(store):
    job = store.add("/videos/a.mp4")
    store.transition(job.id, "running")
    updated = store.progress(job.id, "asr", "전사 중")
    assert updated.status == "running"
    assert updated.stage == "asr"


def test_the_happy_path_runs_all_the_way_to_approved(store):
    job = store.add("/videos/a.mp4")
    for status in ("running", "review", "approved"):
        job = store.transition(job.id, status)
    assert job.status == "approved"


def test_an_approved_job_cannot_be_quietly_requeued(store):
    """이게 뚫리면 승인 안 된 버전이 올라간다."""
    job = store.add("/videos/a.mp4")
    for status in ("running", "review", "approved"):
        store.transition(job.id, status)
    with pytest.raises(TransitionError):
        store.transition(job.id, "queued")


def test_a_queued_job_cannot_jump_straight_to_review(store):
    job = store.add("/videos/a.mp4")
    with pytest.raises(TransitionError):
        store.transition(job.id, "review")


def test_an_approved_job_can_be_sent_back_for_re_review(store):
    job = store.add("/videos/a.mp4")
    for status in ("running", "review", "approved"):
        store.transition(job.id, status)
    assert store.transition(job.id, "review").status == "review"


def test_a_failure_can_be_retried(store):
    job = store.add("/videos/a.mp4")
    store.transition(job.id, "running")
    store.transition(job.id, "failed", error="터짐")
    assert store.transition(job.id, "queued").status == "queued"


def test_results_accumulate_across_transitions(store):
    job = store.add("/videos/a.mp4")
    store.transition(job.id, "running", result={"a": 1})
    final = store.transition(job.id, "review", result={"b": 2})
    assert final.result == {"a": 1, "b": 2}


def test_an_unknown_job_raises(store):
    with pytest.raises(KeyError):
        store.transition("없는거", "running")


def test_counts_cover_every_status(store):
    store.add("/videos/a.mp4")
    counts = store.counts()
    assert set(counts) == set(TRANSITIONS)
    assert counts["queued"] == 1


def test_deleting_a_job_removes_it(store):
    job = store.add("/videos/a.mp4")
    assert store.delete(job.id)
    assert store.get(job.id) is None
    assert not store.delete(job.id)


def test_every_status_has_a_korean_label():
    assert set(STATUS_LABELS) == set(TRANSITIONS)


def test_the_store_survives_a_reopen(tmp_path):
    path = tmp_path / "jobs.sqlite"
    JobStore(path).add("/videos/a.mp4")
    assert len(JobStore(path).list()) == 1
