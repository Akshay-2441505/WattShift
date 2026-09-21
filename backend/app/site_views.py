"""Read models for the Live cluster dashboard: everything one site's page needs, in one call."""
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import tariff as tariff_mod  # called as tariff_mod.tod_zone so a demo's time-lapse patch is picked up
from app.catalogue import Tariff, get_tariff
from app.measure import FINISHED
from app.models import AuditLog, Company, ManagedJob, Site
from app.tariff import IST


def display_state(j: ManagedJob) -> str:
    """The one word the dashboard shows for a job (the frontend turns it into a label and a colour)."""
    if j.state in FINISHED:
        return "done" if j.state == "COMPLETED" else "ended"
    if j.state == "RUNNING":
        return "running"
    if j.plan_status == "released":
        return "released"
    if j.plan_status == "skipped":
        return "skipped"
    if j.plan_status == "abandoned":
        return "owner_changed" if j.note == "user_changed" else "left_alone"
    if j.plan_status == "unplaceable":
        return "no_window"
    if j.plan_status == "planned":
        if j.planned_start is None:
            return "runs_normally"
        return "held" if j.applied_start == j.planned_start else "would_hold"
    return "seen"


def describe(a: AuditLog) -> str:
    d = a.detail or {}
    job = f"job {a.ref}" if a.ref else "a job"
    lines = {
        "job_seen": f"Saw {job}",
        "decision": f"Decided a start time for {job}",
        "applied": f"Set the start time of {job} in Slurm",
        "apply_failed": f"Could not set the start time of {job}; leaving it alone",
        "override": f"The owner changed {job}; no longer managing it",
        "skipped": f"Skipping {job} ({d.get('reason', 'not supported')})",
        "unplaceable": f"No cheaper window for {job}; it runs as normal",
        "released": f"Released {job} to start now",
        "measured": f"{job.capitalize()} finished; saving measured",
        "release_all_on": "Kill switch on: releasing every held job",
        "release_all_off": "Kill switch off",
        "mode_changed": f"Mode changed to {d.get('mode')}",
        "agent_mode": f"Agent connected in {d.get('mode')} mode",
        "mode_mismatch": (
            "The site is in shadow mode: plans are recorded, nothing is sent to the agent" if d.get("site_mode") == "shadow"
            else "The agent is in shadow mode: it reports but never changes Slurm"
        ),
        "shadow_violation": "A shadow agent tried to change Slurm",
        "no_tariff": "No valid tariff: planning paused",
    }
    return lines.get(a.event, a.event.replace("_", " "))


def zone_segments(tariff: Tariff, start: datetime, end: datetime) -> list[dict]:
    """Tariff zones over [start, end): 1-minute steps up to 3 hours (the time-lapse demo has zones minutes wide), else 15."""
    step = timedelta(minutes=1 if end - start <= timedelta(hours=3) else 15)
    t = start.replace(second=0, microsecond=0)
    segs: list[dict] = []
    while t < end:
        zone = tariff_mod.tod_zone(t, tariff.rules).zone
        if segs and segs[-1]["zone"] == zone:
            segs[-1]["end"] = t + step
        else:
            segs.append({"start": t, "end": t + step, "zone": zone})
        t += step
    return segs


def _money(x) -> float | None:
    return None if x is None else float(x)


def _job_row(j: ManagedJob) -> dict:
    return {
        "ref": j.ref, "state": j.state, "plan_status": j.plan_status, "display": display_state(j), "note": j.note, "gpus": j.gpus,
        "time_limit_min": j.time_limit_min, "max_wait_min": j.max_wait_min, "submit_time": j.submit_time,
        "baseline_start": j.baseline_start, "planned_start": j.planned_start, "applied_start": j.applied_start,
        "actual_start": j.actual_start, "actual_end": j.actual_end, "baseline_cost": _money(j.baseline_cost),
        "planned_cost": _money(j.planned_cost), "actual_cost": _money(j.actual_cost), "saved": _money(j.saved),
    }


def build_view(session: Session, site: Site, now: datetime) -> dict:
    company = session.get(Company, site.company_id)
    jobs = session.scalars(
        select(ManagedJob).where(ManagedJob.site_id == site.id).order_by(ManagedJob.submit_time.desc(), ManagedJob.ref).limit(200)
    ).all()
    try:
        tariff = get_tariff(session, site.utility, site.tariff_category, now.astimezone(IST).date())
    except LookupError:
        tariff = None

    measured = [j for j in jobs if j.saved is not None]
    saved = sum(float(j.saved) for j in measured)
    baseline = sum(float(j.baseline_cost) for j in measured)
    potential = sum(
        float(j.baseline_cost) - float(j.planned_cost) for j in jobs
        if j.state == "PENDING" and j.plan_status == "planned" and j.planned_start is not None
        and j.baseline_cost is not None and j.planned_cost is not None
    )
    events = session.scalars(select(AuditLog).where(AuditLog.site_id == site.id).order_by(AuditLog.id.desc()).limit(40)).all()

    zones: list[dict] = []
    if tariff is not None:
        marks = [t for j in jobs for t in (j.planned_start, j.applied_start, j.actual_end) if t is not None]
        past = [t for j in jobs for t in (j.baseline_start, j.actual_start) if t is not None]  # so finished jobs stay on the timeline
        anchors = [*(t - timedelta(minutes=2) for t in past), *(m + timedelta(minutes=10) for m in marks)]
        recent = not anchors or any(a > now - timedelta(hours=1) for a in anchors)  # after a long idle gap the window ends at the last job, not at now
        if recent:
            anchors += [now - timedelta(minutes=5), now + timedelta(minutes=15)]
        lo = max(min(anchors), now - timedelta(hours=36))
        hi = min(max(anchors), now + timedelta(hours=36))
        zones = zone_segments(tariff, lo, hi)

    return {
        "site": {
            "id": str(site.id), "company": company.name, "name": site.name, "mode": site.mode, "release_all": site.release_all,
            "gpus": site.gpus, "last_seen_at": site.last_seen_at, "agent_version": site.agent_version,
            "agent_mode": site.last_agent_mode, "tariff": f"{site.utility} {site.tariff_category}",
        },
        "now": now,
        "summary": {
            "saved": round(saved, 2), "baseline": round(baseline, 2),
            "pct_saved": round(saved / baseline * 100, 2) if baseline else None, "jobs_measured": len(measured),
            "potential": round(potential, 2), "counts": dict(Counter(display_state(j) for j in jobs)),
        },
        "jobs": [_job_row(j) for j in jobs],
        "activity": [{"at": a.at, "actor": a.actor, "event": a.event, "ref": a.ref, "text": describe(a)} for a in events],
        "zones": zones,
    }
