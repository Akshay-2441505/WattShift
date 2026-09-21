import pytest

from tests.fakeslurm import FakeSlurm
from wattshift_agent.runner import ForbiddenCommand, SlurmError
from wattshift_agent.slurm import Slurm, fmt_utc

NOW = 1_790_000_000  # 2026-09-21T14:13:20Z


def test_fmt_utc():
    assert fmt_utc(NOW) == "2026-09-21T14:13:20"
    assert fmt_utc(1789885800) == "2026-09-20T06:30:00"  # the epoch recorded in Spike 0's time-zone test


def test_queue_and_detail():
    fake = FakeSlurm(NOW)
    fake.add("101", gpus=3, limit_min=90)
    s = Slurm(fake)
    [row] = s.queue()
    assert (row.ref, row.state, row.qos, row.submit) == ("101", "PENDING", "flex", NOW)
    d = s.detail("101")
    assert d.gpus == 3 and d.time_limit_min == 90 and d.state == "PENDING" and d.eligible == NOW


def test_a_job_slurm_no_longer_knows_has_no_detail():
    assert Slurm(FakeSlurm(NOW)).detail("999") is None


def test_set_start_holds_the_job_until_then():
    fake = FakeSlurm(NOW)
    fake.add("101")
    s = Slurm(fake)
    s.set_start("101", NOW + 3600)
    assert ["scontrol", "update", "JobId=101", "StartTime=2026-09-21T15:13:20"] in fake.commands
    d = s.detail("101")
    assert d.eligible == NOW + 3600 and d.reason == "BeginTime"
    fake.advance(1800)
    assert s.detail("101").state == "PENDING"
    fake.advance(1801)
    assert s.detail("101").state == "RUNNING"


def test_release_starts_the_job_now():
    fake = FakeSlurm(NOW)
    fake.add("101")
    s = Slurm(fake)
    s.set_start("101", NOW + 3600)
    s.release("101")
    fake.advance(1)
    assert s.detail("101").state == "RUNNING"


def test_finished_jobs_come_from_accounting_not_the_queue():
    fake = FakeSlurm(NOW)
    fake.add("101", runtime_s=60)
    s = Slurm(fake)
    fake.advance(1)
    fake.advance(120)
    assert s.queue() == []
    [a] = s.finished(["101"])
    assert (a.ref, a.state, a.end - a.start) == ("101", "COMPLETED", 60)
    assert s.finished([]) == []


def test_updating_a_job_that_already_started_is_an_error():
    fake = FakeSlurm(NOW)
    fake.add("101")
    fake.advance(1)
    with pytest.raises(SlurmError, match="no longer pending"):
        Slurm(fake).set_start("101", NOW + 3600)


def test_the_shadow_fake_refuses_a_deferral_but_allows_a_release():
    fake = FakeSlurm(NOW, allow_defer=False)
    fake.add("101")
    s = Slurm(fake)
    with pytest.raises(ForbiddenCommand):
        s.set_start("101", NOW + 3600)
    s.release("101")
