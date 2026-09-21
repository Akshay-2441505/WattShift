"""POST /agent/v1/sync: the agent reports what it sees, the cloud answers with start-time decisions (spec section 6).
Everything is keyed by (site, ref), so a retried request changes nothing."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, Field
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app import audit, measure, planner, sites
from app.catalogue import get_tariff
from app.models import ManagedJob, Site
from app.tariff import IST

POLL_SECONDS = 30


def _no_nul(s: str) -> str:
    if "\x00" in s:
        raise ValueError("text cannot contain a NUL character")  # Postgres text cannot hold one: it would be a 500 at insert time
    return s


Text = Annotated[str, AfterValidator(_no_nul)]
# Slurm ids look like 123, 123_4, 123_[1-3], 123+1: digits, letters, and a few separators, nothing else.
Ref = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[0-9A-Za-z_+\[\]\-,.]+$")]
_RANK = {"PENDING": 0, "RUNNING": 1}  # every terminal state ranks 2


class JobFact(BaseModel):
    ref: Ref  # the Slurm job id
    state: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OTHER"]
    submit_time: AwareDatetime | None = None
    gpus: int | None = Field(None, ge=0, le=100_000)
    time_limit_min: int | None = Field(None, ge=1, le=525_600)
    max_wait_min: int | None = Field(None, ge=0, le=525_600)
    predicted_start: AwareDatetime | None = None
    start_time: AwareDatetime | None = None
    end_time: AwareDatetime | None = None
    override: bool = False  # the agent saw a change it did not make
    skipped_reason: Text | None = Field(None, max_length=64)  # array | dependency | requeue: never planned


class Applied(BaseModel):
    ref: Ref
    start_at: AwareDatetime
    ok: bool
    error: Text | None = Field(None, max_length=500)


class SyncIn(BaseModel):
    agent_version: Text = Field(max_length=32)
    mode: Literal["shadow", "autonomous"]
    sent_at: AwareDatetime  # informational
    jobs: list[JobFact] = Field(default_factory=list, max_length=2000)
    applied: list[Applied] = Field(default_factory=list, max_length=2000)
    released: list[Ref] = Field(default_factory=list, max_length=2000)  # refs set to start now after release_all
    release_all: bool = False  # the operator ran release-all on the agent


class DecisionOut(BaseModel):
    ref: str
    start_at: AwareDatetime


class SyncOut(BaseModel):
    decisions: list[DecisionOut]
    release_all: bool
    next_poll_s: int


def _rank(state: str) -> int:
    return _RANK.get(state, 2)


def _record_agent(session: Session, site: Site, body: SyncIn, now: datetime) -> str:
    """Note the agent's heartbeat and return the EFFECTIVE mode: autonomous only if site and agent both say so."""
    site.last_seen_at, site.agent_version = now, body.agent_version
    if site.last_agent_mode != body.mode:
        audit.log(session, site.id, now, "agent", "agent_mode", mode=body.mode, previous=site.last_agent_mode)
        site.last_agent_mode = body.mode
    if site.mode != body.mode:
        audit.log_throttled(session, site.id, now, "planner", "mode_mismatch", site_mode=site.mode, agent_mode=body.mode)
    return "autonomous" if site.mode == "autonomous" and body.mode == "autonomous" else "shadow"


def _upsert_jobs(session: Session, site: Site, body: SyncIn, now: datetime, jobs: dict[str, ManagedJob]) -> None:
    for f in body.jobs:
        j = jobs.get(f.ref)
        if j is None:
            j = ManagedJob(
                site_id=site.id, ref=f.ref, state=f.state, plan_status="pending", first_seen=now,
                submit_time=f.submit_time or now, gpus=f.gpus,
                time_limit_min=f.time_limit_min, max_wait_min=f.max_wait_min, predicted_start=f.predicted_start,
                actual_start=f.start_time, actual_end=f.end_time,
            )
            session.add(j)
            jobs[f.ref] = j
            audit.log(session, site.id, now, "agent", "job_seen", ref=f.ref, state=f.state)
        else:
            if _rank(f.state) < _rank(j.state):
                continue  # a stale (retried or reordered) report never moves a job backwards
            j.state = f.state
            if j.gpus is None and f.gpus is not None:
                j.gpus = f.gpus  # captured at first sight and never overwritten (sacct may not have GPUs later)
            if f.time_limit_min is not None:
                j.time_limit_min = f.time_limit_min
            if f.max_wait_min is not None:
                j.max_wait_min = f.max_wait_min
            if f.predicted_start is not None and j.state == "PENDING" and j.applied_start is None:
                j.predicted_start = f.predicted_start  # it is N/A at first; the baseline freezes once we defer the job
            if f.start_time:
                j.actual_start = f.start_time
            if f.end_time:
                j.actual_end = f.end_time
        if f.override and j.plan_status != "abandoned":
            j.plan_status, j.note = "abandoned", "user_changed"
            audit.log(session, site.id, now, "agent", "override", ref=f.ref)
        elif f.skipped_reason and j.plan_status in planner.MOVABLE:
            j.plan_status, j.note = "skipped", f.skipped_reason
            audit.log(session, site.id, now, "agent", "skipped", ref=f.ref, reason=f.skipped_reason)


def _apply_reports(session: Session, site: Site, body: SyncIn, now: datetime, jobs: dict[str, ManagedJob]) -> None:
    if body.applied and body.mode == "shadow":  # a shadow agent must have no write path: flag it loudly
        audit.log(session, site.id, now, "agent", "shadow_violation", refs=[a.ref for a in body.applied][:50])
    for a in body.applied:
        j = jobs.get(a.ref)
        if j is None:
            audit.log(session, site.id, now, "agent", "unknown_ref", ref=a.ref)
        elif a.ok:
            if j.applied_start != a.start_at:  # a retried report logs once
                j.applied_start = a.start_at
                audit.log(session, site.id, now, "agent", "applied", ref=a.ref, start_at=a.start_at.isoformat())
        elif j.plan_status != "abandoned":  # the agent could not apply it: leave the job alone for good
            j.plan_status, j.note = "abandoned", f"apply_failed: {a.error or 'unknown'}"[:200]
            audit.log(session, site.id, now, "agent", "apply_failed", ref=a.ref, error=a.error)
    for ref in body.released:
        j = jobs.get(ref)
        if j is not None and j.plan_status != "released":
            j.plan_status, j.note = "released", "release_all"
            audit.log(session, site.id, now, "agent", "released", ref=ref)


def process_sync(session: Session, site: Site, body: SyncIn, now: datetime) -> SyncOut:
    """Apply the agent's report, re-plan the site, return what the agent should do. The caller commits."""
    session.execute(text("select pg_advisory_xact_lock(hashtextextended(:sid, 0))"), {"sid": str(site.id)})
    session.refresh(site)  # take the lock first, then read: another request may have changed the switches
    mode = _record_agent(session, site, body, now)
    if body.release_all and not site.release_all:
        sites.set_release_all(session, site, True, now, actor="agent")

    refs = {f.ref for f in body.jobs} | {a.ref for a in body.applied} | set(body.released)
    jobs = {
        j.ref: j
        for j in session.scalars(
            select(ManagedJob).where(
                ManagedJob.site_id == site.id, or_(ManagedJob.ref.in_(refs), ManagedJob.state.in_(("PENDING", "RUNNING")))
            )
        )
    }
    _upsert_jobs(session, site, body, now, jobs)
    _apply_reports(session, site, body, now, jobs)
    for j in jobs.values():  # price any job that just finished (real start and end, spec section 10)
        measure.finalize(session, site, j, now)

    if not site.release_all:
        try:
            tariff = get_tariff(session, site.utility, site.tariff_category, now.astimezone(IST).date())
        except LookupError:
            audit.log_throttled(session, site.id, now, "planner", "no_tariff", utility=site.utility, category=site.tariff_category)
        else:
            planner.plan_site(session, site, tariff, now=now, mode=mode)

    decisions: list[DecisionOut] = []
    if mode == "autonomous" and not site.release_all:
        pending = session.scalars(
            select(ManagedJob)
            .where(
                ManagedJob.site_id == site.id, ManagedJob.state == "PENDING", ManagedJob.plan_status == "planned",
                ManagedJob.planned_start.is_not(None),
            )
            .order_by(ManagedJob.submit_time, ManagedJob.ref)
        )
        decisions = [DecisionOut(ref=j.ref, start_at=j.planned_start) for j in pending if j.planned_start != j.applied_start]
    session.flush()
    return SyncOut(decisions=decisions, release_all=site.release_all, next_poll_s=POLL_SECONDS)
