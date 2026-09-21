"""One agent cycle: read Slurm, keep the local record, tell the cloud, apply what it answers (spec sections 4 and 6)."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from wattshift_agent import __version__
from wattshift_agent.applier import apply_decision, release_all
from wattshift_agent.audit import event
from wattshift_agent.parse import TERMINAL
from wattshift_agent.rules import max_wait_for
from wattshift_agent.state import Tracked

MAX_NEW_PER_CYCLE = 200  # ponytail: bounds the scontrol calls after a restart with a huge backlog; the rest follow next cycle
MAX_JOBS_PER_SYNC = 2000  # the cloud's request limit
ACCT_REF = re.compile(r"\d+(_\d+)?")  # the only job ids sacct is asked about: it refuses ranges like 35_[1-2] for the whole call
GIVE_UP_AFTER_S = 86_400  # a job that left the queue and never shows in accounting is closed as OTHER


def iso(ts: int | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def discover(cfg, slurm, state, rows, now: int, audit=None) -> int:
    """Start tracking pending jobs that match a flex rule. A job matching no rule is never recorded, read further or
    sent. Returns how many jobs were added."""
    added = 0
    for r in rows:
        if r.state != "PENDING" or state.get(r.ref) is not None:
            continue
        wait = max_wait_for(cfg.rules, r)
        if wait is None:
            continue
        if added >= MAX_NEW_PER_CYCLE:
            break
        t = Tracked(ref=r.ref, first_seen=now, max_wait_min=wait, state="PENDING", submit=r.submit, predicted_start=r.start)
        if "_" in r.ref:  # array job (41_3, 41_[1-3]): left alone in v1
            t.skipped_reason = "array"
        else:
            d = slurm.detail(r.ref)
            if d is None:
                continue  # gone between the two calls: nothing to track
            t.gpus, t.time_limit_min = d.gpus, d.time_limit_min
            t.skipped_reason = "dependency" if d.dependency else "requeue" if d.restarts > 0 else None
        state.save(t)
        added += 1
        event(audit, "job_seen", ref=r.ref, max_wait_min=wait, gpus=t.gpus, skipped=t.skipped_reason)
    return added


def _check_override(slurm, t: Tracked, audit) -> None:
    """A job we deferred is compared with what we set. EligibleTime (not StartTime, which also moves with Slurm's own
    estimate) is the begin time; a different one, or any hold, means the owner changed it."""
    d = slurm.detail(t.ref)
    if d is None:
        return  # it left the queue between the calls; the next cycle closes it
    if d.eligible != t.applied_start or d.reason.startswith("JobHeld"):
        t.override = True
        event(audit, "override_detected", ref=t.ref, expected=t.applied_start, eligible=d.eligible, reason=d.reason)


def _close_finished(slurm, state, gone: list[Tracked], now: int, audit) -> None:
    if not gone:
        return
    askable = [t for t in gone if ACCT_REF.fullmatch(t.ref)]
    rows = {a.ref: a for a in slurm.finished([t.ref for t in askable])}
    for t in gone:
        a = rows.get(t.ref)
        if not ACCT_REF.fullmatch(t.ref):  # a pending-array row (35_[1-2]) is replaced by its tasks; sacct refuses the id
            t.state = "OTHER"
            state.save(t)
            event(audit, "job_finished", ref=t.ref, state=t.state)
            continue
        if a is not None and a.state in TERMINAL:
            t.state, t.start_time, t.end_time = a.state, a.start, a.end
            if t.gpus is None and a.gpus is not None:
                t.gpus = a.gpus
        elif a is not None and a.state == "RUNNING":
            t.state, t.start_time = "RUNNING", a.start or t.start_time  # still finishing up
        elif now - t.first_seen > GIVE_UP_AFTER_S:
            t.state = "OTHER"
        else:
            continue  # accounting has not caught up: ask again next cycle
        state.save(t)
        event(audit, "job_finished", ref=t.ref, state=t.state)


def refresh(slurm, state, by_ref: dict, now: int, audit=None) -> None:
    """Bring every tracked job up to date: started, finished (from accounting), or changed by its owner."""
    gone = []
    for t in state.active():
        r = by_ref.get(t.ref)
        if r is None:
            gone.append(t)
            continue
        if r.state == "RUNNING":
            t.state, t.start_time = "RUNNING", r.start or t.start_time
        elif r.state == "PENDING":
            if t.state == "RUNNING":  # started, then put back in the queue: a requeue, not ours to manage
                t.state, t.skipped_reason = "PENDING", t.skipped_reason or "requeue"
            elif t.applied_start is None and not t.released and t.skipped_reason is None:
                t.predicted_start = r.start  # until we defer it, Slurm's own estimate is the baseline
            elif t.applied_start is not None and not t.released and not t.override:
                _check_override(slurm, t, audit)
        state.save(t)
    _close_finished(slurm, state, gone, now, audit)


def observe(cfg, slurm, state, now: int, audit=None) -> None:
    """Everything the agent learns from Slurm in one cycle. Raises SlurmError if Slurm cannot be read."""
    rows = slurm.queue()
    discover(cfg, slurm, state, rows, now, audit)
    refresh(slurm, state, {r.ref: r for r in rows}, now, audit)


@dataclass
class Outgoing:
    payload: dict  # the request body
    applied: list  # refs of the `applied` reports in it (cleared once the cloud accepts them)
    released: list
    final: list  # refs of finished jobs in it (marked sent once the cloud accepts them)
    release_requested: bool


def _fact(t: Tracked) -> dict:
    predicted = t.predicted_start
    if predicted is not None and predicted > t.first_seen + t.max_wait_min * 60:
        predicted = None  # implausible (Slurm's "one year ahead" answer): never sent
    return {
        "ref": t.ref, "state": t.state, "submit_time": iso(t.submit), "gpus": t.gpus, "time_limit_min": t.time_limit_min,
        "max_wait_min": t.max_wait_min, "predicted_start": iso(predicted), "start_time": iso(t.start_time),
        "end_time": iso(t.end_time), "override": t.override, "skipped_reason": t.skipped_reason,
    }


def build_body(cfg, state, now: int) -> Outgoing:
    active = state.active()[:MAX_JOBS_PER_SYNC]
    applied, released = state.applied_outbox(), state.released_outbox()
    payload = {
        "agent_version": __version__, "mode": cfg.mode, "sent_at": iso(now), "jobs": [_fact(t) for t in active],
        "applied": [{"ref": r, "start_at": iso(s), "ok": ok, "error": err} for r, s, ok, err in applied],
        "released": released, "release_all": state.get_meta("release_requested") == "1",
    }
    return Outgoing(payload, [a[0] for a in applied], list(released), [t.ref for t in active if t.state in TERMINAL], payload["release_all"])


@dataclass
class CycleResult:
    next_poll_s: int
    jobs: int
    applied: int
    released: int


def _parse_iso(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def _clamp_poll(asked, default: int) -> int:
    return default if not isinstance(asked, (int, float)) else int(min(120, max(10, asked)))


def run_once(cfg, slurm, state, cloud, now: int, audit=None) -> CycleResult:
    """Read Slurm, tell the cloud, apply the answer. Raises SlurmError (nothing is sent) or CloudError (the record and
    the outboxes are kept, and no job is changed). Either way every job keeps whatever start time it already has."""
    observe(cfg, slurm, state, now, audit)
    released = 0
    if cfg.mode == "shadow":  # shadow only reads; the one write it may make is undoing its own earlier deferrals
        released += release_all(slurm, state, audit)
    out = build_body(cfg, state, now)
    reply = cloud.post_sync(out.payload)

    state.clear_applied(out.applied)
    state.clear_released(out.released)
    state.mark_sent(out.final)
    if out.release_requested and reply.get("release_all"):
        state.set_meta("release_requested", "0")  # the cloud has its switch on; stop asking

    applied = 0
    decisions = reply.get("decisions") or []
    if reply.get("release_all"):
        released += release_all(slurm, state, audit)
    elif cfg.mode == "autonomous":
        for d in decisions:
            apply_decision(cfg, slurm, state, d["ref"], _parse_iso(d["start_at"]), now, audit)
            applied += 1
    elif decisions:
        event(audit, "decisions_ignored_in_shadow", count=len(decisions))
    state.prune(now - 7 * 86_400)
    return CycleResult(_clamp_poll(reply.get("next_poll_s"), cfg.poll_seconds), len(out.payload["jobs"]), applied, released)
