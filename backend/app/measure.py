"""Measuring what deferral saved, from what Slurm really did (spec section 10). Modeled power, real times."""
from datetime import datetime

from sqlalchemy.orm import Session

from app import audit
from app.catalogue import get_tariff
from app.models import ManagedJob, Site
from app.savings import bill_cost
from app.tariff import IST

FINISHED = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OTHER")


def elapsed_minutes(job: ManagedJob) -> int:
    """The job's real runtime in whole minutes (at least 1), from Slurm's start and end."""
    return max(1, round((job.actual_end - job.actual_start).total_seconds() / 60))


def finalize(session: Session, site: Site, job: ManagedJob, now: datetime) -> bool:
    """Price a finished job that WE deferred: baseline (where it would have started) and actual (where it did) are both
    billed on the same real runtime, so the difference is purely the tariff zone. Replaces the time-limit estimate in
    baseline_cost. Negative savings are kept. Returns True only when it wrote a measurement (once per job)."""
    if job.state not in FINISHED or job.applied_start is None or job.actual_cost is not None:
        return False
    if not (job.actual_start and job.actual_end and job.baseline_start):
        return False
    try:
        tariff = get_tariff(session, site.utility, site.tariff_category, job.actual_start.astimezone(IST).date())
    except LookupError:
        audit.log_throttled(session, site.id, now, "planner", "no_tariff_for_measurement", ref=job.ref)
        return False
    minutes = elapsed_minutes(job)
    power = max(job.gpus or 1, 1) * float(site.kw_per_gpu)
    job.baseline_cost = bill_cost(job.baseline_start, minutes, power, tariff.rules, tariff.base_rate)
    job.actual_cost = bill_cost(job.actual_start, minutes, power, tariff.rules, tariff.base_rate)
    job.saved = round(float(job.baseline_cost) - float(job.actual_cost), 4)
    audit.log(session, site.id, now, "planner", "measured", ref=job.ref, saved=float(job.saved), minutes=minutes)
    return True
