"""Fires due jobs and tracks running ones. The jobs table is the source of truth; there are no per-job timers,
so a restart loses nothing. These functions COMMIT (a claim must be visible to other workers before we start)."""
import logging
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import clock
from app.config import settings
from app.models import Job, JobRun
from app.providers.base import ComputeProvider
from app.safety import safe_reason
from app.savings import record_savings

log = logging.getLogger("wattshift.dispatch")

NOT_FOUND_GRACE_TICKS = 30  # ~2.5 min at the 5 s tick before a run the provider does not know about is failed
_misses: dict = {}  # job id -> consecutive 'not found' polls (in-memory; resets on restart, which is harmless)


def _fail(session: Session, job: Job, reason: str) -> None:
    job.status = "failed"
    job.failure_reason = safe_reason(reason, 500)
    session.commit()


def claim(session: Session, job_id, now: datetime) -> bool:
    """Atomic scheduled -> running. Exactly one caller wins, however many ticks/processes race. Commits."""
    won = session.execute(
        update(Job).where(Job.id == job_id, Job.status == "scheduled").values(status="running", executed_at=now)
    ).rowcount
    session.commit()
    return bool(won)


def expire_overdue(session: Session, now: datetime) -> int:
    """Fail jobs whose deadline has passed without starting: they can no longer be useful, so they must not linger
    or run. Covers 'queued' jobs too (no window was ever found)."""
    n = session.execute(
        update(Job)
        .where(Job.status == "scheduled", Job.deadline < now)
        .values(status="failed", failure_reason="deadline passed before the job could start (service was down or backlogged)")
    ).rowcount
    n += session.execute(
        update(Job)
        .where(Job.status == "queued", Job.deadline < now)
        .values(status="failed", failure_reason="no window with capacity was found before the deadline")
    ).rowcount
    session.commit()
    return n


def _running_count(session: Session) -> int:
    """Kaggle allows about two GPU sessions at once, and a 'without' twin is one of them."""
    jobs = session.scalar(select(func.count()).select_from(Job).where(Job.status == "running")) or 0
    twins = session.scalar(select(func.count()).select_from(JobRun).where(JobRun.kind == "without", JobRun.status == "running")) or 0
    return jobs + twins


def _read_power(provider: ComputeProvider, ref: str) -> dict:
    """The run's measured power, or {} when it cannot be read. Losing a reading must never lose the run's result."""
    try:
        return (provider.measure(ref) if hasattr(provider, "measure") else None) or {}
    except Exception:
        log.warning("could not read the power measurement of run %s", ref, exc_info=True)
        return {}


def fire_due_jobs(
    session: Session, providers: dict[str, ComputeProvider], now: datetime | None = None, max_running: int | None = None
) -> list[Job]:
    """Start due jobs, at most `max_running` at once (excess due jobs simply wait, still before their deadline).
    # ponytail: one global cap; make it per-provider when a second provider exists."""
    now = now or clock.now()
    limit = settings.max_concurrent_runs if max_running is None else max_running
    expire_overdue(session, now)
    running = _running_count(session)
    due = session.scalars(
        select(Job.id)
        .where(Job.status == "scheduled", Job.assigned_window_start <= now)
        .order_by(Job.assigned_window_start)
        .limit(max(0, limit - running))
    ).all()
    fired = []
    for job_id in due:
        if not claim(session, job_id, now):
            continue
        job = session.get(Job, job_id, populate_existing=True)
        provider = providers.get(job.provider)
        if provider is None:
            _fail(session, job, f"provider {job.provider!r} is not configured")
        else:
            try:
                job.external_ref = provider.start(job)
            except Exception as e:  # one bad job must not block the rest of the tick
                log.exception("start failed for job %s", job_id)
                _fail(session, job, f"start failed: {type(e).__name__}: {e}")
            else:
                session.commit()
                fired.append(job)
    return fired


def poll_running(session: Session, providers: dict[str, ComputeProvider]) -> list[Job]:
    """Resolve running jobs. Returns jobs that finished successfully (Phase 4 writes their savings)."""
    done = []
    running = session.scalars(select(Job).where(Job.status == "running", Job.external_ref.is_not(None))).all()
    for job in running:
        provider = providers.get(job.provider)
        if provider is None:
            continue
        try:
            state = provider.status(job.external_ref)
        except LookupError:
            # The provider has no such run. Briefly normal right after a start; if it persists the start never took.
            _misses[job.id] = _misses.get(job.id, 0) + 1
            if _misses[job.id] >= NOT_FOUND_GRACE_TICKS:
                _misses.pop(job.id, None)
                _fail(session, job, f"run {job.external_ref} not found on the provider (start did not take effect)")
            continue
        except Exception:
            log.warning("status check failed for job %s; will retry", job.id, exc_info=True)
            continue  # transient (network, API hiccup): try again next tick
        _misses.pop(job.id, None)
        if state == "done":
            job.status = "done"
            record_savings(session, job)  # same commit: a done job and its ledger row appear together
            if job.provider == "kaggle":
                session.add(JobRun(job_id=job.id, kind="with", status="done", external_ref=job.external_ref, started_at=job.executed_at,
                                   simulated=clock.is_sim(), **_read_power(provider, job.external_ref)))
            session.commit()
            done.append(job)
        elif state == "failed":
            _fail(session, job, "provider reported the run as failed")
    return done


def fire_twins(session: Session, providers: dict[str, ComputeProvider], now: datetime | None = None, max_running: int | None = None) -> int:
    """Start the 'without Wattshift' twins: a second real run of each Kaggle job, started at submission so it is priced
    at the tariff of the moment the job arrived. Real jobs take the free slots first (call this after fire_due_jobs).
    Returns how many were started."""
    now = now or clock.now()
    limit = settings.max_concurrent_runs if max_running is None else max_running
    free = max(0, limit - _running_count(session))
    ids = session.scalars(
        select(JobRun.id).join(Job, Job.id == JobRun.job_id)
        .where(JobRun.kind == "without", JobRun.status == "scheduled").order_by(Job.submitted_at).limit(free)
    ).all()
    started = 0
    for run_id in ids:
        won = session.execute(
            update(JobRun).where(JobRun.id == run_id, JobRun.status == "scheduled").values(status="running", started_at=now)
        ).rowcount
        session.commit()
        if not won:
            continue
        run = session.get(JobRun, run_id, populate_existing=True)
        job = session.get(Job, run.job_id)
        provider = providers.get(job.provider)
        try:
            if provider is None:
                raise RuntimeError(f"provider {job.provider!r} is not configured")
            run.external_ref = provider.start(job, tag="without")
            started += 1
        except Exception as e:  # a failed twin costs the comparison, never the job
            log.exception("twin start failed for job %s", job.id)
            run.status, run.failure_reason = "failed", safe_reason(f"start failed: {type(e).__name__}: {e}", 500)
        session.commit()
    return started


def poll_twins(session: Session, providers: dict[str, ComputeProvider]) -> None:
    """Resolve running twins: store the measured power when done, mark failed runs. Same retry-next-tick rules as jobs."""
    runs = session.scalars(select(JobRun).where(JobRun.kind == "without", JobRun.status == "running", JobRun.external_ref.is_not(None))).all()
    for run in runs:
        provider = providers.get(session.get(Job, run.job_id).provider)
        if provider is None:
            continue
        try:
            state = provider.status(run.external_ref)
        except Exception:
            log.warning("status check failed for twin of job %s; will retry", run.job_id, exc_info=True)
            continue
        if state == "done":
            run.status = "done"
            for k, v in _read_power(provider, run.external_ref).items():
                setattr(run, k, v)
        elif state == "failed":
            run.status, run.failure_reason = "failed", "provider reported the run as failed"
        session.commit()


def recover_on_startup(session: Session) -> int:
    """A job stuck 'running' with no provider handle was interrupted between claim and start. Single-instance
    assumption: nothing else is mid-start when we boot. Overdue 'scheduled' jobs need no recovery, they fire on tick."""
    n = session.execute(
        update(Job)
        .where(Job.status == "running", Job.external_ref.is_(None))
        .values(status="failed", failure_reason="interrupted before provider start (service restarted)")
    ).rowcount
    session.commit()
    return n
