import time

import pytest

from tests.conftest import NOW
from tests.fakecloud import FakeCloud
from tests.fakeslurm import FakeSlurm
from wattshift_agent.cli import Deps, main
from wattshift_agent.cycle import iso
from wattshift_agent.slurm import Slurm
from wattshift_agent.state import State

CONFIG = """
cloud: {{url: 'https://cloud.test'}}
mode: {mode}
rules:
  - match: {{qos: flex}}
    max_wait: 24h
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WATTSHIFT_SITE_KEY", "wsk_test")
    fake, cloud = FakeSlurm(NOW), FakeCloud()

    def write(mode="autonomous"):
        p = tmp_path / "agent.yaml"
        p.write_text(CONFIG.format(mode=mode))
        return str(p)

    def factory(cfg):
        return Deps(Slurm(fake), State(cfg.state_path), cloud, None)

    return write, factory, fake, cloud, tmp_path


def run(env, *args, mode="autonomous"):
    write, factory, *_ = env
    return main(["-c", write(mode), *args], factory=factory)


def test_run_once_does_a_cycle(env):
    _, _, fake, cloud, _ = env
    fake.add("101")
    assert run(env, "run", "--once") == 0
    assert [j["ref"] for j in cloud.bodies[0]["jobs"]] == ["101"]


def test_run_once_fails_with_a_message_when_the_cloud_is_down(env, capsys):
    _, _, fake, cloud, _ = env
    cloud.fail = True
    assert run(env, "run", "--once") == 1
    assert "connection refused" in capsys.readouterr().err


def test_a_bad_config_exits_2(env, capsys):
    write, factory, *_ = env
    bad = write()
    open(bad, "w").write("mode: yolo\n")
    assert main(["-c", bad, "status"], factory=factory) == 2
    assert "config error" in capsys.readouterr().err


def test_release_all_works_without_the_cloud_and_asks_the_cloud_to_follow(env, capsys):
    _, factory, fake, cloud, tmp_path = env
    fake.add("101")
    # `run` uses the real clock, so the decision is an hour from the real now (not from the fake's fixed NOW)
    cloud.reply(decisions=[{"ref": "101", "start_at": iso(int(time.time()) + 3600)}])
    run(env, "run", "--once")
    cloud.fail = True  # the kill switch must not depend on the cloud
    fake.commands.clear()
    assert run(env, "release-all") == 0
    assert ["scontrol", "update", "JobId=101", "StartTime=now"] in fake.commands
    assert "released 1 job" in capsys.readouterr().out
    cloud.fail = False
    cloud.reply(release_all=True)
    run(env, "run", "--once")
    assert cloud.bodies[-1]["release_all"] is True and cloud.bodies[-1]["released"] == ["101"]


def test_status_summarises_the_record(env, capsys):
    _, _, fake, cloud, _ = env
    fake.add("101")
    fake.add("41_[1-3]")
    run(env, "run", "--once")
    capsys.readouterr()
    assert run(env, "status") == 0
    out = capsys.readouterr().out
    assert "PENDING" in out and "skipped" in out and "mode: autonomous" in out


def test_check_passes_on_a_healthy_setup(env, capsys):
    _, _, fake, cloud, _ = env
    fake.add("101")
    assert run(env, "check") == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and "squeue" in out and "cloud" in out


def test_check_fails_and_says_which_step_when_slurm_is_unreachable(env, capsys):
    _, _, fake, cloud, _ = env
    fake.down = True
    assert run(env, "check") == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "Unable to contact slurm controller" in out


def test_check_warns_when_times_do_not_come_back_as_epoch(env, capsys):
    _, _, fake, cloud, _ = env
    fake.add("101")
    fake.jobs["101"]["submit"] = "2026-09-21T14:13:20"  # a cluster that ignores SLURM_TIME_FORMAT
    assert run(env, "check") == 1
    assert "epoch" in capsys.readouterr().out
