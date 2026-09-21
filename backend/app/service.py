import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import clock
from app.allocator import BLOCK, Block, NoCapacityError, allocate, floor_block, overlaps
from app.config import settings
from app.models import Job, JobRun, PriceSignal
from app.savings import bill_cost
from app.seed import load_rules
from app.tariff import tod_multiplier

PROVIDERS = ("kaggle", "github_actions")
MAX_DURATION_MIN = 180
LOCK_KEY = 7_770_001  # serialises every ledger read-modify-write (held until the transaction ends)


class TooManyActiveJobs(Exception):
    """The queue is full: too many jobs are already waiting or running."""


def active_source() -> str:
    return "iex_dam_replay" if clock.is_sim() else "iex_dam"


def _book(ledger: dict[datetime, int], start: datetime, duration_minutes: int, weight: int = 1) -> None:
    for block, minutes in overlaps(start, duration_minutes):
        ledger[block] = ledger.get(block, 0) + minutes * weight  # weight = GPUs, matching allocate(weight=...)


def derive_ledger(session: Session) -> dict[datetime, int]:
    """Minutes already booked per 15-min block, derived from live job rows so it can never drift.
    # ponytail: full scan of active jobs; add a ledger table if active jobs exceed ~10k."""
    ledger: dict[datetime, int] = {}
    jobs = session.scalars(
        select(Job).where(Job.status.in_(("scheduled", "running")), Job.assigned_window_start.is_not(None))
    )
    for j in jobs:
        _book(ledger, j.assigned_window_start, j.duration_minutes)
    return ledger


def wait_for_prices(blocks: list[Block], start: datetime, duration_minutes: int, deadline: datetime, now: datetime) -> bool:
    """True when a job should stay queued instead of taking `start`: the prices stop before its deadline (the next
    day's are not published yet) and the best window known so far is right now, inside the freeze margin plus one
    re-plan interval, where a later price update might no longer be able to move it. Waiting stops when the priced
    horizon is about to run out, so a job never waits itself out of the only windows it has."""
    margin = timedelta(minutes=settings.freeze_minutes, seconds=settings.replan_seconds)
    priced_to = blocks[-1].start + BLOCK if blocks else now
    if priced_to >= deadline or start > now + margin:
        return False
    return now < priced_to - timedelta(minutes=duration_minutes) - margin - BLOCK


def validate(duration_minutes: int, deadline: datetime, provider: str, power_kw: float | None, now: datetime) -> None:
    if not 1 <= duration_minutes <= MAX_DURATION_MIN:
        raise ValueError(f"duration_minutes must be 1..{MAX_DURATION_MIN}")
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    if deadline.tzinfo is None:
        raise ValueError("deadline must be timezone-aware")
    if deadline < now + timedelta(minutes=duration_minutes):
        raise ValueError("deadline is too soon for the job to finish")
    if power_kw is not None and not 0 < power_kw <= 1000:
        raise ValueError("power_kw must be in (0, 1000]")


def submit_job(
    session: Session,
    duration_minutes: int,
    deadline: datetime,
    provider: str,
    power_kw: float | None = None,
    *,
    now: datetime | None = None,
    cap_per_block: int | None = None,
) -> Job:
    """Place a job in the cheapest window that fits before its deadline. Returns status 'scheduled', or
    'queued' (no window, never dropped) when nothing fits — the reforecast loop retries it later.
    The caller commits; the advisory lock is released at that point."""
    now = now or clock.now()
    validate(duration_minutes, deadline, provider, power_kw, now)
    cap = cap_per_block or settings.cap_minutes_per_block

    session.execute(text("select pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
    active = session.scalar(select(func.count()).select_from(Job).where(Job.status.in_(("queued", "scheduled", "running"))))
    if (active or 0) >= settings.max_active_jobs:
        raise TooManyActiveJobs(f"{active} jobs are already waiting or running (the limit is {settings.max_active_jobs}); try again when some have finished")

    rules = load_rules(session)
    prices = session.execute(
        select(PriceSignal.ts, PriceSignal.price_rs_per_mwh)
        .where(PriceSignal.source == active_source(), PriceSignal.ts >= floor_block(now), PriceSignal.ts < deadline)
        .order_by(PriceSignal.ts)
    )
    blocks = [Block(ts, float(p) * tod_multiplier(ts, rules)) for ts, p in prices]

    power = Decimal(str(power_kw if power_kw is not None else settings.default_power_kw))
    job = Job(
        id=uuid.uuid4(), duration_minutes=duration_minutes, deadline=deadline, provider=provider,
        power_kw=power, status="queued", submitted_at=now,  # app clock, so replay mode shows simulated time
        baseline_cost_rs=bill_cost(now, duration_minutes, power, rules, settings.base_rate_rs_kwh),
    )
    try:
        start = allocate(str(job.id), duration_minutes, now, deadline, blocks, derive_ledger(session), cap)
    except NoCapacityError:
        pass
    else:
        if not wait_for_prices(blocks, start, duration_minutes, deadline, now):
            job.status = "scheduled"
            job.assigned_window_start = start
            job.planned_cost_rs = bill_cost(start, duration_minutes, power, rules, settings.base_rate_rs_kwh)
    session.add(job)
    session.flush()
    if provider == "kaggle":  # the 'without Wattshift' run of the same job, started at once (see dispatch.fire_twins)
        session.add(JobRun(job_id=job.id, kind="without", status="scheduled", simulated=clock.is_sim()))
        session.flush()
    return job


def replan_pending(
    session: Session, *, now: datetime | None = None, cap_per_block: int | None = None, freeze_minutes: int | None = None
) -> dict[str, int]:
    """Re-run allocation for jobs that have not started, against the latest prices (spec layer 3, F9).

    Re-planned: 'queued' jobs (no window yet, e.g. tomorrow's prices were not published at submission) and
    'scheduled' jobs whose window is beyond the freeze margin. Pinned: running jobs and jobs about to fire; their
    capacity stays booked. Jobs are re-allocated in submission order with the same merit-order allocator, so
    unchanged prices change nothing. A scheduled job that cannot be re-placed keeps its window (a bad price refresh
    must never unschedule work). The caller commits.
    # ponytail: naive full re-run, no hysteresis; add a minimum-improvement threshold if jobs churn between windows."""
    now = now or clock.now()
    cap = cap_per_block or settings.cap_minutes_per_block
    horizon = now + timedelta(minutes=settings.freeze_minutes if freeze_minutes is None else freeze_minutes)
    stats = {"placed": 0, "moved": 0, "still_queued": 0, "unchanged": 0}

    session.execute(text("select pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})  # same lock as submit_job

    ledger: dict[datetime, int] = {}
    movable: list[Job] = []
    active = session.scalars(
        # Submission order, so an unchanged world re-plans to the same windows. Ties (same microsecond) fall back to id.
        select(Job).where(Job.status.in_(("queued", "scheduled", "running"))).order_by(Job.submitted_at, Job.id)
    )
    for j in active:
        if j.status == "queued" or (j.status == "scheduled" and j.assigned_window_start and j.assigned_window_start > horizon):
            movable.append(j)
        elif j.assigned_window_start:
            _book(ledger, j.assigned_window_start, j.duration_minutes)  # pinned: keeps its capacity
    if not movable:
        return stats

    rules = load_rules(session)
    prices = session.execute(
        select(PriceSignal.ts, PriceSignal.price_rs_per_mwh)
        .where(
            PriceSignal.source == active_source(),
            PriceSignal.ts >= floor_block(now),
            PriceSignal.ts < max(j.deadline for j in movable),
        )
        .order_by(PriceSignal.ts)
    )
    blocks = [Block(ts, float(p) * tod_multiplier(ts, rules)) for ts, p in prices]

    for j in movable:
        try:
            start = allocate(str(j.id), j.duration_minutes, now, j.deadline, blocks, ledger, cap)
        except NoCapacityError:
            if j.status == "scheduled":
                _book(ledger, j.assigned_window_start, j.duration_minutes)  # keep the old window and its capacity
                stats["unchanged"] += 1
            else:
                stats["still_queued"] += 1
            continue
        if j.status == "queued" and wait_for_prices(blocks, start, j.duration_minutes, j.deadline, now):
            stats["still_queued"] += 1
            continue
        _book(ledger, start, j.duration_minutes)
        if j.status == "queued":
            j.status = "scheduled"
            stats["placed"] += 1
        elif start != j.assigned_window_start:
            stats["moved"] += 1
        else:
            stats["unchanged"] += 1
            continue
        j.assigned_window_start = start
        j.planned_cost_rs = bill_cost(start, j.duration_minutes, j.power_kw, rules, settings.base_rate_rs_kwh)
    session.flush()
    return stats
