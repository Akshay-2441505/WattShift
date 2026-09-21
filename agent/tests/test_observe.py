import pytest

from tests.conftest import NOW
from wattshift_agent import cycle
from wattshift_agent.cycle import build_body, iso, observe


def shows(fake, ref):
    return ["scontrol", "show", "job", ref] in fake.commands


def test_iso():
    assert iso(NOW) == "2026-09-21T14:13:20Z" and iso(None) is None


def test_only_jobs_matching_a_rule_are_ever_read(cfg, fake, slurm, state):
    fake.add("1", qos="normal")
    fake.add("2", qos="flex")
    observe(cfg, slurm, state, NOW)
    assert state.get("1") is None and state.get("2") is not None
    assert not shows(fake, "1") and shows(fake, "2")


def test_first_sight_facts_are_recorded_once(cfg, fake, slurm, state):
    fake.add("101", gpus=3, limit_min=90)
    observe(cfg, slurm, state, NOW)
    t = state.get("101")
    assert (t.first_seen, t.gpus, t.time_limit_min, t.max_wait_min, t.state, t.submit) == (NOW, 3, 90, 1440, "PENDING", NOW)
    observe(cfg, slurm, state, NOW + 30)
    assert fake.commands.count(["scontrol", "show", "job", "101"]) == 1  # detail is read once, not every poll
    assert state.get("101").first_seen == NOW


def test_a_job_already_running_at_first_sight_is_ignored(cfg, fake, slurm, state):
    fake.add("101", state="RUNNING")
    fake.jobs["101"]["start"] = NOW - 10
    observe(cfg, slurm, state, NOW)
    assert state.get("101") is None


@pytest.mark.parametrize(
    "ref, extra, reason",
    [("41_[1-3]", {}, "array"), ("42", {"dependency": "afterok:40(unfulfilled)"}, "dependency"), ("43", {"restarts": 1}, "requeue")],
)
def test_jobs_v1_leaves_alone_are_recorded_with_a_reason(cfg, fake, slurm, state, ref, extra, reason):
    fake.add(ref, **extra)
    observe(cfg, slurm, state, NOW)
    assert state.get(ref).skipped_reason == reason
    if reason == "array":
        assert not shows(fake, ref)  # arrays are recognised from the id, no detail call


def test_a_job_that_vanishes_between_the_two_calls_is_not_tracked(cfg, fake, slurm, state):
    fake.add("777")
    fake.jobs["777"]["hide_detail"] = True  # squeue lists it, then scontrol says "Invalid job id"
    observe(cfg, slurm, state, NOW)
    assert state.get("777") is None


def test_at_most_a_few_new_jobs_are_read_per_cycle(cfg, fake, slurm, state, monkeypatch):
    monkeypatch.setattr(cycle, "MAX_NEW_PER_CYCLE", 2)
    for i in range(5):
        fake.add(str(100 + i))
    for expected in (2, 4, 5):
        observe(cfg, slurm, state, NOW)
        assert len(state.active()) == expected


def test_predicted_start_is_recorded_and_guarded(cfg, fake, slurm, state):
    fake.add("101", predicted=NOW + 7200)
    fake.add("102", predicted=NOW + 365 * 86400)  # Slurm's "exactly one year" answer when running jobs have no limit
    observe(cfg, slurm, state, NOW)
    facts = {f["ref"]: f for f in build_body(cfg, state, NOW).payload["jobs"]}
    assert facts["101"]["predicted_start"] == iso(NOW + 7200)
    assert facts["102"]["predicted_start"] is None  # beyond first_seen + max_wait: dropped from the request


def test_a_late_prediction_is_picked_up_until_we_defer_the_job(cfg, fake, slurm, state):
    fake.add("101")
    observe(cfg, slurm, state, NOW)
    assert state.get("101").predicted_start is None  # N/A for the first ~30 s
    fake.jobs["101"]["predicted"] = NOW + 600
    observe(cfg, slurm, state, NOW + 30)
    assert state.get("101").predicted_start == NOW + 600
    slurm.set_start("101", NOW + 3600)  # we defer it: the baseline freezes
    t = state.get("101")
    t.applied_start = NOW + 3600
    state.save(t)
    fake.jobs["101"]["predicted"] = NOW + 9000
    observe(cfg, slurm, state, NOW + 60)
    assert state.get("101").predicted_start == NOW + 600


def test_running_then_finished_is_read_from_accounting(cfg, fake, slurm, state):
    fake.add("101", runtime_s=60)
    observe(cfg, slurm, state, NOW)
    fake.advance(1)
    observe(cfg, slurm, state, NOW + 1)
    t = state.get("101")
    assert t.state == "RUNNING" and t.start_time == NOW
    fake.advance(120)
    observe(cfg, slurm, state, NOW + 121)
    t = state.get("101")
    assert (t.state, t.start_time, t.end_time) == ("COMPLETED", NOW, NOW + 60)
    assert ["sacct", "-j", "101"] == [c for c in fake.commands if c[0] == "sacct"][0][:3]


def test_a_job_cancelled_while_pending_has_no_start(cfg, fake, slurm, state):
    fake.add("101")
    observe(cfg, slurm, state, NOW)
    fake.cancel("101")
    observe(cfg, slurm, state, NOW + 10)
    t = state.get("101")
    assert t.state == "CANCELLED" and t.start_time is None and t.end_time == NOW


def test_accounting_that_never_catches_up_is_given_up_after_a_day(cfg, fake, slurm, state):
    fake.accounting = False
    fake.add("101", runtime_s=60)
    observe(cfg, slurm, state, NOW)
    fake.advance(1)
    observe(cfg, slurm, state, NOW + 1)  # we see it running...
    fake.advance(120)  # ...then it finishes, but accounting knows nothing yet
    observe(cfg, slurm, state, NOW + 121)
    assert state.get("101").state == "RUNNING"  # unchanged: no evidence yet, so ask again next cycle
    observe(cfg, slurm, state, NOW + 86_401)
    assert state.get("101").state == "OTHER"


def test_a_pending_array_row_that_leaves_the_queue_never_breaks_the_cycle(cfg, fake, slurm, state):
    """Found on a real Slurm: once array task 1 starts, the row `35_[1-2]` disappears and sacct refuses that id, which
    used to fail every later cycle for as long as the job stayed known."""
    fake.add("35_[1-2]")
    observe(cfg, slurm, state, NOW)
    del fake.jobs["35_[1-2]"]  # squeue now shows 35_1 (running) and 35_[2] (pending) instead
    observe(cfg, slurm, state, NOW + 10)
    assert state.get("35_[1-2]").state == "OTHER"  # v1 never manages arrays, so there is no result to measure
    assert not any("[" in c[2] for c in fake.commands if c[0] == "sacct")
    observe(cfg, slurm, state, NOW + 20)  # and the next cycle is fine


def test_requeue_after_start_stops_management(cfg, fake, slurm, state):
    fake.add("101")
    observe(cfg, slurm, state, NOW)
    fake.advance(1)
    observe(cfg, slurm, state, NOW + 1)
    fake.jobs["101"].update(state="PENDING", state_text="PENDING", restarts=1, start=None, begin=NOW + 900)
    observe(cfg, slurm, state, NOW + 5)
    t = state.get("101")
    assert t.state == "PENDING" and t.skipped_reason == "requeue"


def deferred(cfg, fake, slurm, state, when=NOW + 3600):
    fake.add("101")
    observe(cfg, slurm, state, NOW)
    slurm.set_start("101", when)
    t = state.get("101")
    t.applied_start = when
    state.save(t)


def test_our_own_deferral_is_not_an_override_even_if_slurm_predicts_later(cfg, fake, slurm, state):
    deferred(cfg, fake, slurm, state)
    fake.jobs["101"]["predicted"] = NOW + 9000  # cluster busy: the shown start moves, the begin time does not
    observe(cfg, slurm, state, NOW + 30)
    assert state.get("101").override is False


def test_the_owner_changing_the_start_time_is_an_override(cfg, fake, slurm, state):
    deferred(cfg, fake, slurm, state)
    fake.user_set_start("101", NOW + 60)
    observe(cfg, slurm, state, NOW + 30)
    assert state.get("101").override is True


def test_the_owner_holding_the_job_is_an_override(cfg, fake, slurm, state):
    deferred(cfg, fake, slurm, state)
    fake.jobs["101"]["held"] = True
    observe(cfg, slurm, state, NOW + 30)
    assert state.get("101").override is True


def test_the_body_lists_facts_and_nothing_private(cfg, fake, slurm, state):
    fake.add("101", user="alice", account="secret-lab", name="private-project-x", gpus=2, limit_min=30)
    observe(cfg, slurm, state, NOW)
    out = build_body(cfg, state, NOW)
    assert out.payload["jobs"] == [{
        "ref": "101", "state": "PENDING", "submit_time": iso(NOW), "gpus": 2, "time_limit_min": 30, "max_wait_min": 1440,
        "predicted_start": None, "start_time": None, "end_time": None, "override": False, "skipped_reason": None,
    }]
    text = str(out.payload)
    assert "alice" not in text and "secret-lab" not in text and "private-project-x" not in text
    assert out.payload["mode"] == "autonomous" and out.payload["sent_at"] == iso(NOW)
    assert out.payload["applied"] == [] and out.payload["released"] == [] and out.payload["release_all"] is False


def test_the_body_carries_the_outboxes_and_the_release_request(cfg, fake, slurm, state):
    fake.add("101")
    observe(cfg, slurm, state, NOW)
    state.put_applied("101", NOW + 3600, True, None)
    state.put_applied("102", NOW + 7200, False, "Invalid user id for job 102")
    state.add_released("103")
    state.set_meta("release_requested", "1")
    out = build_body(cfg, state, NOW)
    assert out.payload["applied"] == [
        {"ref": "101", "start_at": iso(NOW + 3600), "ok": True, "error": None},
        {"ref": "102", "start_at": iso(NOW + 7200), "ok": False, "error": "Invalid user id for job 102"},
    ]
    assert out.payload["released"] == ["103"] and out.payload["release_all"] is True
    assert (out.applied, out.released, out.release_requested) == (["101", "102"], ["103"], True)


def test_finished_jobs_are_reported_until_the_report_is_acknowledged(cfg, fake, slurm, state):
    fake.add("101", runtime_s=10)
    observe(cfg, slurm, state, NOW)
    fake.advance(1)
    fake.advance(60)
    observe(cfg, slurm, state, NOW + 61)
    out = build_body(cfg, state, NOW + 61)
    assert out.final == ["101"] and out.payload["jobs"][0]["state"] == "COMPLETED"
    assert build_body(cfg, state, NOW + 62).final == ["101"]  # still there: nothing marked it sent
    state.mark_sent(out.final)
    assert build_body(cfg, state, NOW + 63).payload["jobs"] == []
