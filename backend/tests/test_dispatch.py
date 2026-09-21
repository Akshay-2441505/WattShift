import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import delete, text

from app import db, dispatch
from app.models import Job, JobRun
from app.providers.fake import FakeProvider
from tests.helpers import NOW


def mk(session, *, start=NOW, status="scheduled", provider="kaggle", deadline=None, ref=None, dur=15):
    j = Job(
        duration_minutes=dur, deadline=deadline or NOW + timedelta(hours=5), provider=provider,
        status=status, assigned_window_start=start, external_ref=ref,
    )
    session.add(j)
    session.flush()
    return j


def reload(session, job):
    session.expire_all()
    return session.get(Job, job.id)


def test_fires_only_when_due(session):
    fake = FakeProvider()
    j = mk(session, start=NOW + timedelta(hours=1))
    assert dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW) == []
    assert reload(session, j).status == "scheduled" and fake.started == []

    dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW + timedelta(hours=1))
    j = reload(session, j)
    assert j.status == "running"
    assert j.external_ref == f"fake/{j.id}"
    assert j.executed_at == NOW + timedelta(hours=1)  # records the actual start time
    assert len(fake.started) == 1


def test_a_second_tick_does_not_refire(session):
    fake = FakeProvider()
    mk(session)
    dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW)
    dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW + timedelta(seconds=5))
    assert len(fake.started) == 1


def test_overdue_job_still_fires_before_its_deadline(session):
    """Restart scenario: the service was down past the assigned window."""
    fake = FakeProvider()
    j = mk(session, start=NOW - timedelta(minutes=40), deadline=NOW + timedelta(hours=2))
    dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW)
    assert reload(session, j).status == "running"


def test_job_past_its_deadline_fails_without_starting(session):
    fake = FakeProvider()
    j = mk(session, start=NOW - timedelta(hours=3), deadline=NOW - timedelta(hours=1))
    dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW)
    j = reload(session, j)
    assert j.status == "failed" and "deadline" in j.failure_reason
    assert fake.started == []


def test_provider_start_failure_marks_failed(session):
    j = mk(session)
    dispatch.fire_due_jobs(session, {"kaggle": FakeProvider(fail_start=True)}, now=NOW)
    j = reload(session, j)
    assert j.status == "failed" and "kaggle exploded" in j.failure_reason
    assert j.external_ref is None


def test_unconfigured_provider_marks_failed(session):
    j = mk(session, provider="github_actions")
    dispatch.fire_due_jobs(session, {"kaggle": FakeProvider()}, now=NOW)
    j = reload(session, j)
    assert j.status == "failed" and "not configured" in j.failure_reason


def test_one_bad_job_does_not_block_others(session):
    bad = mk(session, provider="github_actions", start=NOW - timedelta(minutes=1))
    good = mk(session)
    dispatch.fire_due_jobs(session, {"kaggle": FakeProvider()}, now=NOW)
    assert reload(session, bad).status == "failed"
    assert reload(session, good).status == "running"


def test_concurrency_cap_holds_extra_due_jobs_until_a_slot_frees(session):
    """Kaggle allows only ~2 concurrent GPU sessions; firing more silently loses the extra pushes."""
    fake = FakeProvider()
    jobs = [mk(session, start=NOW - timedelta(minutes=i)) for i in range(4)]
    fired = dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW, max_running=2)
    assert len(fired) == 2 and len(fake.started) == 2
    assert sorted(reload(session, j).status for j in jobs) == ["running", "running", "scheduled", "scheduled"]
    # nothing more fires while both slots are busy
    assert dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW, max_running=2) == []
    # one finishes -> exactly one waiting job takes the slot
    fake.statuses[fake.started[0]] = "done"
    dispatch.poll_running(session, {"kaggle": fake})
    assert len(dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW, max_running=2)) == 1
    assert sorted(reload(session, j).status for j in jobs) == ["done", "running", "running", "scheduled"]


def test_waiting_jobs_still_respect_the_deadline(session):
    fake = FakeProvider()
    mk(session, status="running", ref="r/hold")  # occupies the only slot
    late = mk(session, start=NOW - timedelta(hours=2), deadline=NOW - timedelta(minutes=1))
    ok = mk(session, start=NOW - timedelta(hours=1))
    dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW, max_running=1)
    j = reload(session, late)
    # expired even though no slot is free: a backlog must never run a job past its deadline, nor let it linger
    assert j.status == "failed" and "deadline" in j.failure_reason
    assert reload(session, ok).status == "scheduled" and fake.started == []  # still waiting for the slot


def test_kernel_that_never_appears_is_failed_after_the_grace_period_not_retried_forever(session):
    fake = FakeProvider()
    lost = mk(session, status="running", ref="r/lost")
    ok = mk(session, status="running", ref="r/ok")
    fake.statuses["r/lost"] = LookupError("kernel not found")
    for _ in range(dispatch.NOT_FOUND_GRACE_TICKS - 1):
        dispatch.poll_running(session, {"kaggle": fake})
    assert reload(session, lost).status == "running"  # still inside the grace period
    fake.statuses["r/lost"] = LookupError("kernel not found")
    dispatch.poll_running(session, {"kaggle": fake})
    lost = reload(session, lost)
    assert lost.status == "failed" and "not found" in lost.failure_reason
    assert reload(session, ok).status == "running"


def test_a_kernel_that_appears_late_resets_its_miss_count(session):
    fake = FakeProvider()
    j = mk(session, status="running", ref="r/slow")
    fake.statuses["r/slow"] = LookupError("propagating")
    for _ in range(dispatch.NOT_FOUND_GRACE_TICKS - 1):
        dispatch.poll_running(session, {"kaggle": fake})
    fake.statuses["r/slow"] = "running"  # it showed up
    dispatch.poll_running(session, {"kaggle": fake})
    fake.statuses["r/slow"] = LookupError("blip")
    dispatch.poll_running(session, {"kaggle": fake})
    assert reload(session, j).status == "running"  # counter restarted, one blip does not fail it


def test_poll_running_resolves_done_failed_and_leaves_running(session):
    fake = FakeProvider()
    done = mk(session, status="running", ref="r/done")
    bad = mk(session, status="running", ref="r/bad")
    busy = mk(session, status="running", ref="r/busy")
    flaky = mk(session, status="running", ref="r/flaky")
    fake.statuses = {"r/done": "done", "r/bad": "failed", "r/flaky": ConnectionError("net down")}
    finished = dispatch.poll_running(session, {"kaggle": fake})
    assert {j.id for j in finished} == {done.id}  # only successful completions are returned (savings hook)
    assert reload(session, done).status == "done"
    bad = reload(session, bad)
    assert bad.status == "failed" and bad.failure_reason
    assert reload(session, busy).status == "running"
    assert reload(session, flaky).status == "running"  # transient error: retry next tick, never crash


def test_recover_on_startup_fails_only_jobs_interrupted_before_start(session):
    lost = mk(session, status="running", ref=None)
    live = mk(session, status="running", ref="r/live")
    waiting = mk(session, status="scheduled", start=NOW - timedelta(minutes=5))
    assert dispatch.recover_on_startup(session) == 1
    lost = reload(session, lost)
    assert lost.status == "failed" and "interrupted" in lost.failure_reason
    assert reload(session, live).status == "running"
    assert reload(session, waiting).status == "scheduled"  # will fire on the next tick


def test_claim_succeeds_exactly_once_per_job(session):
    """Deterministic guard for the atomic claim (two workers that both saw the job as due)."""
    j = mk(session)
    assert dispatch.claim(session, j.id, NOW) is True
    assert dispatch.claim(session, j.id, NOW) is False
    assert dispatch.claim(session, j.id, NOW + timedelta(seconds=5)) is False


def test_concurrent_ticks_fire_a_job_exactly_once(engine):
    import threading

    factory = db.make_session_factory(engine)
    with factory() as s, s.begin():
        mk(s)
    fake = FakeProvider(start_delay=0.5)  # widen the race window
    barrier = threading.Barrier(4)

    def tick(_):
        with factory() as s:
            s.execute(text("select 1"))  # open the connection first so all ticks race, not the TLS handshakes
            barrier.wait()
            dispatch.fire_due_jobs(s, {"kaggle": fake}, now=NOW)

    try:
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(tick, range(4)))
        assert len(fake.started) == 1
    finally:
        with factory() as s, s.begin():
            s.execute(delete(JobRun))
            s.execute(delete(Job))


def test_runner_fires_a_job_that_became_due_while_the_service_was_down(engine):
    """Integration: real APScheduler + recovery + dispatcher against the DB."""
    from app import runner

    factory = db.make_session_factory(engine)
    with factory() as s, s.begin():
        j = mk(s, start=NOW - timedelta(minutes=30), deadline=NOW + timedelta(hours=2))
        job_id = j.id
    fake = FakeProvider()
    sched = runner.start(factory, {"kaggle": fake}, dispatch_seconds=1, ingest=False, now=lambda: NOW)
    try:
        deadline = time.time() + 10
        job = None
        while time.time() < deadline:
            with factory() as s:
                job = s.get(Job, job_id)
            if job.external_ref:  # 'running' alone is set at claim time, before the provider call returns
                break
            time.sleep(0.3)
        assert job.status == "running" and job.external_ref
        assert len(fake.started) == 1
    finally:
        sched.shutdown(wait=False)
        with factory() as s, s.begin():
            s.execute(delete(JobRun))
            s.execute(delete(Job))
