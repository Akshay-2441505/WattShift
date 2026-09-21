import pytest

from tests.conftest import NOW, make_cfg
from tests.fakeslurm import FakeSlurm
from wattshift_agent.applier import apply_decision, release_all
from wattshift_agent.slurm import Slurm
from wattshift_agent.state import Tracked


def track(state, fake, ref="101", **kw):
    fake.add(ref)
    t = Tracked(ref=ref, first_seen=NOW, max_wait_min=1440, state="PENDING", gpus=4, time_limit_min=60, **kw)
    state.save(t)
    return t


def updates(fake):
    return [c for c in fake.commands if c[:2] == ["scontrol", "update"]]


def test_a_decision_sets_the_start_time_in_utc_and_is_confirmed(cfg, fake, slurm, state):
    track(state, fake)
    assert apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW) is True
    assert updates(fake) == [["scontrol", "update", "JobId=101", "StartTime=2026-09-21T15:13:20"]]
    assert state.get("101").applied_start == NOW + 3600
    assert state.applied_outbox() == [("101", NOW + 3600, True, None)]


def test_a_start_time_slurm_did_not_keep_is_reported_as_a_failure(cfg, fake, slurm, state):
    track(state, fake)
    fake.silent_updates.add("101")
    assert apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW) is False
    assert state.get("101").applied_start is None
    assert state.applied_outbox() == [("101", NOW + 3600, False, "Slurm did not keep the start time")]


def test_a_slurm_error_is_reported_and_the_job_is_left_alone(cfg, fake, slurm, state):
    track(state, fake)
    fake.fail_updates.add("101")
    assert apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW) is False
    assert state.get("101").applied_start is None
    assert state.applied_outbox()[0][2:] == (False, "Invalid user id for job 101")


def test_shadow_mode_never_defers(tmp_path, state):
    shadow, fake = make_cfg(tmp_path, "shadow"), FakeSlurm(NOW, allow_defer=False)
    track(state, fake)
    assert apply_decision(shadow, Slurm(fake), state, "101", NOW + 3600, NOW) is False
    assert updates(fake) == []
    assert state.applied_outbox() == [("101", NOW + 3600, False, "agent is in shadow mode")]


@pytest.mark.parametrize(
    "kw, why",
    [
        ({"state": "RUNNING"}, "running"),
        ({"skipped_reason": "array"}, "no longer managed"),
        ({"override": True}, "no longer managed"),
        ({"released": True}, "no longer managed"),
    ],
)
def test_jobs_that_are_not_ours_to_change_are_refused(cfg, fake, slurm, state, kw, why):
    t = track(state, fake)
    for k, v in kw.items():
        setattr(t, k, v)
    state.save(t)
    assert apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW) is False
    assert updates(fake) == [] and why in state.applied_outbox()[0][3]


def test_an_unknown_job_is_refused(cfg, fake, slurm, state):
    assert apply_decision(cfg, slurm, state, "555", NOW + 3600, NOW) is False
    assert state.applied_outbox()[0][3] == "not a job this agent manages"


def test_a_start_time_beyond_the_flex_limit_is_refused(cfg, fake, slurm, state):
    track(state, fake)
    too_far = NOW + 2 * 1440 * 60 + 301  # first_seen + 2 x max_wait + 5 minutes
    assert apply_decision(cfg, slurm, state, "101", too_far, NOW) is False
    assert updates(fake) == [] and "beyond the flex limit" in state.applied_outbox()[0][3]
    assert apply_decision(cfg, slurm, state, "101", NOW + 2 * 1440 * 60 + 300, NOW) is True  # exactly at the bound


def test_a_decision_for_now_releases_the_job(cfg, fake, slurm, state):
    track(state, fake)
    assert apply_decision(cfg, slurm, state, "101", NOW - 50, NOW) is True
    assert updates(fake) == [["scontrol", "update", "JobId=101", "StartTime=now"]]
    assert state.get("101").released is True


def test_a_repeated_decision_changes_nothing(cfg, fake, slurm, state):
    track(state, fake)
    apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW)
    apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW + 30)
    assert len(updates(fake)) == 1
    assert state.applied_outbox() == [("101", NOW + 3600, True, None)]


def test_release_all_undoes_only_our_own_pending_deferrals(cfg, fake, slurm, state):
    for ref in ("1", "2", "3", "4"):
        track(state, fake, ref)
    for ref in ("1", "2"):  # deferred by us
        apply_decision(cfg, slurm, state, ref, NOW + 3600, NOW)
    t4 = state.get("4")  # a job we deferred but that has since started
    t4.applied_start, t4.state = NOW + 100, "RUNNING"
    state.save(t4)
    fake.commands.clear()
    assert release_all(slurm, state) == 2
    assert sorted(c[2] for c in updates(fake)) == ["JobId=1", "JobId=2"]
    assert all(c[3] == "StartTime=now" for c in updates(fake))
    assert state.released_outbox() == ["1", "2"]
    assert state.get("1").released and not state.get("3").released  # "3" was never deferred by us
    assert release_all(slurm, state) == 0  # nothing left to release


def test_release_all_works_in_shadow_mode(tmp_path, state):
    fake = FakeSlurm(NOW, allow_defer=False)
    track(state, fake)
    t = state.get("101")
    t.applied_start = NOW + 3600  # deferred earlier, while the agent was autonomous
    state.save(t)
    assert release_all(Slurm(fake), state) == 1
    assert updates(fake) == [["scontrol", "update", "JobId=101", "StartTime=now"]]


def test_a_failed_release_is_retried_next_time(cfg, fake, slurm, state):
    track(state, fake)
    apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW)
    fake.fail_updates.add("101")
    assert release_all(slurm, state) == 0 and state.get("101").released is False
    fake.fail_updates.clear()
    assert release_all(slurm, state) == 1
