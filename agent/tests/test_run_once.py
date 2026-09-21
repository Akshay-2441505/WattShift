import pytest

from tests.conftest import NOW, make_cfg
from tests.fakeslurm import FakeSlurm
from wattshift_agent.client import CloudError
from wattshift_agent.cycle import iso, run_once
from wattshift_agent.runner import SlurmError
from wattshift_agent.slurm import Slurm
from wattshift_agent.state import State, Tracked


def updates(fake):
    return [c for c in fake.commands if c[:2] == ["scontrol", "update"]]


def test_a_job_is_deferred_confirmed_started_and_reported(cfg, fake, slurm, state, cloud):
    fake.add("101", gpus=4, limit_min=60, runtime_s=300)
    cloud.reply(decisions=[{"ref": "101", "start_at": iso(NOW + 3600)}])
    r1 = run_once(cfg, slurm, state, cloud, NOW)
    assert [j["ref"] for j in cloud.bodies[0]["jobs"]] == ["101"] and cloud.bodies[0]["applied"] == []
    assert r1.applied == 1 and state.get("101").applied_start == NOW + 3600

    fake.advance(30)
    run_once(cfg, slurm, state, cloud, NOW + 30)
    assert cloud.bodies[1]["applied"] == [{"ref": "101", "start_at": iso(NOW + 3600), "ok": True, "error": None}]
    assert state.applied_outbox() == []  # the cloud accepted the report

    fake.advance(3600)  # Slurm starts it at the set time, with no help from the agent
    run_once(cfg, slurm, state, cloud, NOW + 3630)
    assert cloud.bodies[2]["jobs"][0]["state"] == "RUNNING"
    assert cloud.bodies[2]["jobs"][0]["start_time"] == iso(NOW + 3600)

    fake.advance(400)
    run_once(cfg, slurm, state, cloud, NOW + 4030)
    assert cloud.bodies[3]["jobs"][0]["state"] == "COMPLETED" and cloud.bodies[3]["jobs"][0]["end_time"] == iso(NOW + 3900)
    run_once(cfg, slurm, state, cloud, NOW + 4060)
    assert cloud.bodies[4]["jobs"] == []  # the final report was acknowledged, so it is not repeated
    assert len(updates(fake)) == 1


def test_a_cloud_outage_changes_nothing_in_slurm_and_keeps_the_reports(cfg, fake, slurm, state, cloud):
    fake.add("101")
    cloud.reply(decisions=[{"ref": "101", "start_at": iso(NOW + 3600)}])
    run_once(cfg, slurm, state, cloud, NOW)
    before = list(fake.commands)
    cloud.fail = True
    with pytest.raises(CloudError):
        run_once(cfg, slurm, state, cloud, NOW + 30)
    new = fake.commands[len(before):]
    assert all(c[0] in ("squeue", "sacct") or c[:3] == ["scontrol", "show", "job"] for c in new)  # reads only
    assert state.applied_outbox() == [("101", NOW + 3600, True, None)]  # still queued for the next successful sync
    assert fake.jobs["101"]["begin"] == NOW + 3600  # and the start time we set is still in force


def test_what_was_seen_before_an_outage_is_kept(cfg, fake, slurm, state, cloud):
    fake.add("101")
    cloud.fail = True
    with pytest.raises(CloudError):
        run_once(cfg, slurm, state, cloud, NOW)
    assert state.get("101") is not None


def test_a_slurm_failure_sends_nothing(cfg, fake, slurm, state, cloud):
    fake.down = True
    with pytest.raises(SlurmError):
        run_once(cfg, slurm, state, cloud, NOW)
    assert cloud.bodies == []


def test_shadow_mode_reports_but_ignores_decisions(tmp_path, cloud):
    cfg, fake = make_cfg(tmp_path, "shadow"), FakeSlurm(NOW, allow_defer=False)
    fake.add("101")
    cloud.reply(decisions=[{"ref": "101", "start_at": iso(NOW + 3600)}])  # a misbehaving cloud
    r = run_once(cfg, Slurm(fake), State(tmp_path / "s.db"), cloud, NOW)
    assert cloud.bodies[0]["mode"] == "shadow" and len(cloud.bodies[0]["jobs"]) == 1
    assert updates(fake) == [] and r.applied == 0


def test_switching_to_shadow_undoes_our_earlier_deferrals(tmp_path, cloud):
    cfg, fake, state = make_cfg(tmp_path, "shadow"), FakeSlurm(NOW, allow_defer=False), State(tmp_path / "s.db")
    fake.add("101")
    state.save(Tracked(ref="101", first_seen=NOW, max_wait_min=1440, state="PENDING", gpus=4, time_limit_min=60, applied_start=NOW + 3600))
    fake.user_set_start("101", NOW + 3600)  # the deferral is still in force in Slurm
    run_once(cfg, Slurm(fake), state, cloud, NOW)
    assert updates(fake) == [["scontrol", "update", "JobId=101", "StartTime=now"]]
    assert cloud.bodies[0]["released"] == ["101"]


def test_release_all_from_the_cloud_undoes_deferrals_and_ignores_decisions(cfg, fake, slurm, state, cloud):
    fake.add("101")
    fake.add("102")
    cloud.reply(decisions=[{"ref": "101", "start_at": iso(NOW + 3600)}])
    run_once(cfg, slurm, state, cloud, NOW)
    cloud.reply(release_all=True, decisions=[{"ref": "102", "start_at": iso(NOW + 7200)}])
    r = run_once(cfg, slurm, state, cloud, NOW + 30)
    assert r.released == 1 and state.get("102").applied_start is None  # the decision alongside release_all was ignored
    assert ["scontrol", "update", "JobId=101", "StartTime=now"] in fake.commands
    run_once(cfg, slurm, state, cloud, NOW + 60)
    assert cloud.bodies[2]["released"] == ["101"] and state.released_outbox() == []


def test_a_release_requested_by_the_operator_is_sent_until_acknowledged(cfg, fake, slurm, state, cloud):
    state.set_meta("release_requested", "1")
    run_once(cfg, slurm, state, cloud, NOW)  # the cloud has not turned its switch on yet
    assert cloud.bodies[0]["release_all"] is True and state.get_meta("release_requested") == "1"
    cloud.reply(release_all=True)
    run_once(cfg, slurm, state, cloud, NOW + 30)
    assert cloud.bodies[1]["release_all"] is True and state.get_meta("release_requested") == "0"
    run_once(cfg, slurm, state, cloud, NOW + 60)
    assert cloud.bodies[2]["release_all"] is False


def test_a_redelivered_decision_does_not_change_slurm_twice(cfg, fake, slurm, state, cloud):
    fake.add("101")
    d = {"decisions": [{"ref": "101", "start_at": iso(NOW + 3600)}]}
    cloud.reply(**d)
    cloud.reply(**d)
    run_once(cfg, slurm, state, cloud, NOW)
    run_once(cfg, slurm, state, cloud, NOW + 30)
    assert len(updates(fake)) == 1


@pytest.mark.parametrize("asked, want", [(5, 10), (30, 30), (9999, 120)])
def test_the_polling_interval_is_taken_from_the_cloud_within_limits(cfg, slurm, state, cloud, asked, want):
    cloud.reply(next_poll_s=asked)
    assert run_once(cfg, slurm, state, cloud, NOW).next_poll_s == want


def test_the_default_polling_interval_applies_when_the_cloud_says_nothing(cfg, slurm, state, cloud):
    cloud.replies.append({"next_poll_s": None})
    assert run_once(cfg, slurm, state, cloud, NOW).next_poll_s == cfg.poll_seconds
