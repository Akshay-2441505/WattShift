"""Cloud planner for jobs a site agent reported: pick a start time in the cheapest window, never past the hard limit.
Reuses the demo's allocator (merit order over 15-minute blocks, GPU-weighted capacity) and bill maths."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.allocator import BLOCK_MIN, Block, NoCapacityError, allocate, floor_block
from app.catalogue import Tariff
from app.config import settings
from app.models import Decision, ManagedJob, PriceSignal, Site
from app.savings import bill_cost
from app.service import _book, active_source
from app.tariff import tod_multiplier

MOVABLE = ("pending", "planned", "unplaceable")  # statuses the planner may (re)plan; the rest are hands-off


def plausible_predicted(predicted: datetime | None, first_seen: datetime, max_wait_min: int | None) -> datetime | None:
    """Slurm's predicted start is N/A at first and exactly +1 year when running jobs have no time limit (Spike 0), so
    accept it only if present and no later than first_seen + max_wait."""
    if predicted is None or max_wait_min is None:
        return None
    return predicted if predicted <= first_seen + timedelta(minutes=max_wait_min) else None


def baseline_of(job: ManagedJob) -> datetime:
    """When the job would have started without Wattshift: the plausible prediction, else the moment we first saw it."""
    p = plausible_predicted(job.predicted_start, job.first_seen, job.max_wait_min)
    return max(p, job.first_seen) if p else job.first_seen


def site_cap(site: Site) -> int:
    """GPU-minutes of shifted work allowed per 15-minute block: a share of the site's GPUs, and never more than the
    site's power limit allows. # ponytail: the cloud cannot see the site's other load, so the power limit caps shifted
    load only; add measured site load to cap total demand."""
    cap = float(site.shift_capacity_share) * site.gpus * BLOCK_MIN
    if site.power_limit_kw is not None:
        cap = min(cap, float(site.power_limit_kw) / float(site.kw_per_gpu) * BLOCK_MIN)
    return int(cap)


def _mark_unplaceable(session: Session, site: Site, job: ManagedJob, reason: str, now: datetime, stats: dict) -> None:
    stats["unplaceable"] += 1
    if job.plan_status != "unplaceable":  # audit the change, not every 30 s poll
        job.plan_status, job.note = "unplaceable", reason
        audit.log(session, site.id, now, "planner", "unplaceable", ref=job.ref, reason=reason)


def plan_site(session: Session, site: Site, tariff: Tariff, *, now: datetime, mode: str) -> dict[str, int]:
    """(Re)plan every pending managed job of the site. The caller holds the site's advisory lock and commits.
    # ponytail: naive full re-run each sync with no hysteresis; add a minimum-improvement threshold if jobs churn."""
    margin = timedelta(seconds=site.start_margin_s)
    horizon = now + timedelta(minutes=settings.freeze_minutes)
    cap = site_cap(site)
    kw_per_gpu = float(site.kw_per_gpu)
    stats = {"deferred": 0, "normal": 0, "unchanged": 0, "unplaceable": 0}

    jobs = session.scalars(
        select(ManagedJob)
        .where(ManagedJob.site_id == site.id, ManagedJob.state.in_(("PENDING", "RUNNING")))
        .order_by(ManagedJob.submit_time, ManagedJob.ref)  # submission order: an unchanged world re-plans identically
    ).all()

    ledger: dict[datetime, int] = {}
    movable: list[ManagedJob] = []
    for j in jobs:
        w = max(j.gpus or 1, 1)
        if j.state == "RUNNING":
            if j.applied_start and j.actual_start and j.time_limit_min:  # only work WE shifted uses shifted capacity
                _book(ledger, j.actual_start, j.time_limit_min, w)
        elif j.plan_status in MOVABLE:
            if j.applied_start and j.applied_start <= horizon:  # about to start: pinned, keeps its capacity
                if j.time_limit_min:
                    _book(ledger, j.applied_start, j.time_limit_min, w)
            else:
                movable.append(j)
    if not movable:
        return stats

    windows: dict = {}  # job id -> (baseline, earliest, latest start)
    for j in movable:
        if j.time_limit_min is None or j.max_wait_min is None:
            continue
        base = baseline_of(j)
        j.baseline_start = base
        windows[j.id] = (base, max(now, base), base + timedelta(minutes=j.max_wait_min) - margin)
    last_end = max((windows[j.id][2] + timedelta(minutes=j.time_limit_min) for j in movable if j.id in windows), default=now)
    prices = session.execute(
        select(PriceSignal.ts, PriceSignal.price_rs_per_mwh)
        .where(PriceSignal.source == active_source(), PriceSignal.ts >= floor_block(now), PriceSignal.ts < last_end)
        .order_by(PriceSignal.ts)
    )
    blocks = [Block(ts, float(p) * tod_multiplier(ts, tariff.rules)) for ts, p in prices]

    for j in movable:
        w = max(j.gpus or 1, 1)
        if j.id not in windows:
            _mark_unplaceable(session, site, j, "missing_time_limit_or_max_wait", now, stats)
            continue
        base, earliest, latest = windows[j.id]
        dur, power = j.time_limit_min, w * kw_per_gpu
        try:
            start = allocate(str(j.id), dur, earliest, latest + timedelta(minutes=dur), blocks, ledger, cap, weight=w)
        except NoCapacityError:
            if j.plan_status == "planned" and j.planned_start:  # a bad price refresh must never unschedule a planned job
                _book(ledger, j.planned_start, dur, w)
                stats["unchanged"] += 1
            else:
                _mark_unplaceable(session, site, j, "no_window", now, stats)
            continue

        baseline_cost = bill_cost(base, dur, power, tariff.rules, tariff.base_rate)
        planned_cost = bill_cost(start, dur, power, tariff.rules, tariff.base_rate)
        if planned_cost < baseline_cost:  # defer only for a strictly lower bill
            target = start
        else:  # not worth deferring; if a deferral is already set in Slurm, pull it back to "now"
            target = earliest if j.applied_start else None
            planned_cost = bill_cost(target or base, dur, power, tariff.rules, tariff.base_rate)
        if target is not None:
            _book(ledger, target, dur, w)

        changed = j.plan_status != "planned" or j.planned_start != target
        j.plan_status, j.note = "planned", None
        j.baseline_cost, j.planned_cost = baseline_cost, planned_cost
        if not changed:
            stats["unchanged"] += 1
            continue
        j.planned_start = target
        if target is None:
            stats["normal"] += 1
            continue
        stats["deferred"] += 1
        session.add(Decision(
            managed_job_id=j.id, site_id=site.id, created_at=now, start_at=target, mode=mode,
            baseline_start=base, baseline_cost=baseline_cost, planned_cost=planned_cost,
        ))
        audit.log(session, site.id, now, "planner", "decision", ref=j.ref, start_at=target.isoformat(), mode=mode)
    session.flush()
    return stats
