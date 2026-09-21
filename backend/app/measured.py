"""What the two real runs of each Kaggle job cost: measured watts x the tariff of the hour each run really started.

The comparison is per GPU-hour on purpose. A run lasts about 30 seconds, so its own bill is a fraction of a paisa;
the honest number is what one GPU-hour at the measured draw would cost at that hour's tariff. The two runs draw
about the same power, so any difference between them comes from when they started."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Job, JobRun
from app.seed import load_rules
from app.tariff import tod_multiplier, tod_zone

LIMIT = 50  # newest jobs shown; the summary covers all of them


def _run_view(run: JobRun | None, job: Job, kind: str, rules) -> dict:
    """One run as the page shows it. The 'with' run may not exist yet: then its state is the job's own."""
    if run is None:
        return {"status": job.status if kind == "with" else "scheduled", "started_at": None, "planned_start": job.assigned_window_start if kind == "with" else None,
                "zone": None, "avg_watts": None, "energy_wh": None, "rs_per_gpu_hour": None}
    watts = float(run.avg_watts) if run.avg_watts is not None else None
    started = run.started_at
    return {
        "status": run.status, "started_at": started, "planned_start": None,
        "zone": tod_zone(started, rules).zone if started else None,
        "avg_watts": watts, "energy_wh": float(run.energy_wh) if run.energy_wh is not None else None,
        "rs_per_gpu_hour": round(watts / 1000 * settings.base_rate_rs_kwh * tod_multiplier(started, rules), 4) if watts is not None and started else None,
    }


def build(session: Session, now: datetime) -> dict:
    rules = load_rules(session)
    jobs = session.scalars(select(Job).where(Job.provider == "kaggle", Job.id.in_(select(JobRun.job_id))).order_by(Job.submitted_at.desc(), Job.id)).all()
    by_job: dict = {}
    for r in session.scalars(select(JobRun)):
        by_job.setdefault(r.job_id, {})[r.kind] = r

    rows, gpu, limit = [], None, None
    for j in jobs:
        runs = by_job.get(j.id, {})
        without, with_ = _run_view(runs.get("without"), j, "without", rules), _run_view(runs.get("with"), j, "with", rules)
        complete = without["rs_per_gpu_hour"] is not None and with_["rs_per_gpu_hour"] is not None
        row = {
            "job_id": str(j.id), "submitted_at": j.submitted_at, "job_status": j.status, "complete": complete, "without": without, "with": with_,
            "simulated": any(r.simulated for r in runs.values()),
            "saved_rs_per_gpu_hour": round(without["rs_per_gpu_hour"] - with_["rs_per_gpu_hour"], 4) if complete else None,
        }
        row["pct_saved"] = round(row["saved_rs_per_gpu_hour"] / without["rs_per_gpu_hour"] * 100, 2) if complete and without["rs_per_gpu_hour"] else None
        rows.append(row)
        for r in runs.values():
            if r.gpu_model:
                gpu, limit = r.gpu_model, float(r.power_limit_w) if r.power_limit_w is not None else limit

    pairs = [r for r in rows if r["complete"]]
    n = len(pairs)
    mean = lambda vals: round(sum(vals) / len(vals), 4) if vals else None  # noqa: E731
    cost_without, cost_with = mean([p["without"]["rs_per_gpu_hour"] for p in pairs]), mean([p["with"]["rs_per_gpu_hour"] for p in pairs])
    return {
        "basis": {
            "gpu": gpu or "Tesla T4", "power_limit_w": limit if limit is not None else 70, "per": "GPU-hour",
            "tariff": "Maharashtra MSEDCL HT industrial, time of day", "base_rate_rs_kwh": settings.base_rate_rs_kwh,
        },
        "summary": {
            "pairs": n, "simulated_pairs": sum(1 for p in pairs if p["simulated"]), "waiting": sum(1 for r in rows if not r["complete"] and r["job_status"] in ("queued", "scheduled", "running")),
            "avg_watts_without": mean([p["without"]["avg_watts"] for p in pairs]), "avg_watts_with": mean([p["with"]["avg_watts"] for p in pairs]),
            "rs_per_gpu_hour_without": cost_without, "rs_per_gpu_hour_with": cost_with,
            "saved_rs_per_gpu_hour": round(cost_without - cost_with, 4) if n else None,
            "pct_saved": round((cost_without - cost_with) / cost_without * 100, 2) if n and cost_without else None,
        },
        "jobs": rows[:LIMIT],
    }
