"""Turning cloud decisions into Slurm changes: set one start time, or undo one of our own (spec section 8)."""
from wattshift_agent.audit import event
from wattshift_agent.runner import SlurmError

NOW_SLACK_S = 5  # a decision this close to (or before) now is a release


def apply_decision(cfg, slurm, state, ref: str, start_at: int, now: int, audit=None) -> bool:
    """Apply one decision and queue the outcome for the next sync. Never raises for a refusal or a Slurm error: it
    reports ok=False with a reason, and the cloud then stops managing the job."""

    def done(ok: bool, error: str | None = None) -> bool:
        state.put_applied(ref, start_at, ok, error)
        event(audit, "apply", ref=ref, start_at=start_at, ok=ok, error=error)
        return ok

    if cfg.mode != "autonomous":
        return done(False, "agent is in shadow mode")
    t = state.get(ref)
    if t is None:
        return done(False, "not a job this agent manages")
    if t.state != "PENDING":
        return done(False, f"job is {t.state.lower()}, not pending")
    if t.skipped_reason or t.override or t.released:
        return done(False, "job is no longer managed")
    if start_at > t.first_seen + 2 * t.max_wait_min * 60 + 300:  # a safety net: latest = baseline + max_wait <= first_seen + 2 x max_wait
        return done(False, "start time is beyond the flex limit")
    if start_at <= now + NOW_SLACK_S:
        try:
            slurm.release(ref)
        except SlurmError as e:
            return done(False, str(e)[:200])
        t.released = True
        state.save(t)
        return done(True)
    if t.applied_start == start_at:
        return done(True)  # already set: a repeated decision
    try:
        slurm.set_start(ref, start_at)
        d = slurm.detail(ref)
    except SlurmError as e:
        return done(False, str(e)[:200])
    if d is None or d.eligible != start_at:
        return done(False, "Slurm did not keep the start time")
    t.applied_start = start_at
    state.save(t)
    return done(True)


def release_all(slurm, state, audit=None) -> int:
    """Set every job we deferred back to 'start now'. Allowed in every mode: it only undoes our own changes."""
    released = 0
    for t in state.active():
        if t.applied_start is None or t.released or t.state != "PENDING":
            continue
        try:
            slurm.release(t.ref)
        except SlurmError as e:
            event(audit, "release_failed", ref=t.ref, error=str(e)[:200])
            continue  # tried again next cycle; if the job has started meanwhile it is no longer pending and is skipped
        t.released = True
        state.save(t)
        state.add_released(t.ref)
        released += 1
        event(audit, "released", ref=t.ref)
    return released
