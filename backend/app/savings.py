"""What a DISCOM bill would charge for a job (ToD tariff): planned/baseline cost, the savings ledger and its summary."""
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.allocator import overlaps
from app.config import settings
from app.models import Job, SavingsLog
from app.seed import load_rules
from app.tariff import IST, TodRule, tod_multiplier

log = logging.getLogger("wattshift.savings")


def bill_cost(start: datetime, duration_min: int, power_kw: float, rules: list[TodRule], base_rate_rs_kwh: float) -> float:
    """Rs = sum over spanned 15-min blocks of hours x kW x base rate x ToD multiplier."""
    return round(
        sum(m / 60 * float(power_kw) * base_rate_rs_kwh * tod_multiplier(bs, rules) for bs, m in overlaps(start, duration_min)),
        4,
    )


def record_savings(session: Session, job: Job) -> SavingsLog | None:
    """Baseline = run-now cost fixed at submission; actual = bill for when it REALLY started (executed_at), so a
    job that ran late shows its lost saving honestly. Negative savings are kept, never clamped. Idempotent."""
    existing = session.get(SavingsLog, job.id)
    if existing:
        return existing
    if job.baseline_cost_rs is None or job.executed_at is None:
        log.warning("job %s has no baseline/start time; no savings row written", job.id)
        return None
    actual = bill_cost(job.executed_at, job.duration_minutes, job.power_kw, load_rules(session), settings.base_rate_rs_kwh)
    baseline = float(job.baseline_cost_rs)
    row = SavingsLog(job_id=job.id, baseline_cost_rs=baseline, actual_cost_rs=actual, saved_rs=round(baseline - actual, 4))
    session.add(row)
    session.flush()
    return row


def summary(session: Session, now: datetime) -> dict:
    """Dashboard numbers. Days are IST calendar days of the job's start time, so it follows the sim clock in replay.
    # ponytail: aggregated in Python; move to a SQL GROUP BY if the ledger grows past ~100k rows."""
    today = now.astimezone(IST).date()
    rows = session.execute(
        select(Job.executed_at, SavingsLog.saved_rs, SavingsLog.baseline_cost_rs).join(Job, Job.id == SavingsLog.job_id)
    ).all()
    by_day: dict = defaultdict(float)
    total = baseline_total = 0.0
    for ts, saved, baseline in rows:
        by_day[ts.astimezone(IST).date()] += float(saved)
        total += float(saved)
        baseline_total += float(baseline)
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    daily = [{"date": d.isoformat(), "saved": round(by_day.get(d, 0.0), 2)} for d in days]
    week = sum(d["saved"] for d in daily)
    return {
        "total": round(total, 2),
        "today": daily[-1]["saved"],
        "week": round(week, 2),
        "daily_avg": round(week / 7, 2),
        "daily": daily,
        "jobs_counted": len(rows),
        "baseline_total": round(baseline_total, 2),
        "pct_saved": round(total / baseline_total * 100, 2) if baseline_total else None,
    }
