"""Measured power on the Kaggle T4: a job is run twice (a 'without' twin at submission, the real run at its window),
both runs report their watts, and each is priced at the tariff of the hour it really started."""
import json
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app import dispatch, main, measured, service
from app.config import settings
from app.models import Job, JobRun
from app.providers.fake import FakeProvider
from app.providers.kaggle import KaggleProvider, parse_power
from app.seed import seed_tod
from app.tariff import IST
from tests.helpers import NOW, PEAK_MULT, add_prices, evening_to_next_noon

BASE = settings.base_rate_rs_kwh
PEAK = datetime(2026, 7, 1, 19, tzinfo=IST)  # x PEAK_MULT
SOLAR = datetime(2026, 7, 2, 12, tzinfo=IST)  # x 0.85

LINE = 'WATTSHIFT_POWER {"avg_watts": 67.6, "peak_watts": 69.5, "energy_wh": 0.545, "samples": 30, "gpu": "Tesla T4", "power_limit_w": 70.0}'


# --- reading the number out of a kernel log ---------------------------------------------------------------------


def test_parse_power_reads_the_summary_line_out_of_a_log():
    log = f"wattshift job x started\ndevice cuda\n{LINE}\nwattshift job x finished\n"
    assert parse_power(log) == {
        "avg_watts": 67.6, "peak_watts": 69.5, "energy_wh": 0.545, "samples": 30, "gpu_model": "Tesla T4", "power_limit_w": 70.0,
    }


@pytest.mark.parametrize("log", ["", "no summary here\n", "WATTSHIFT_POWER not-json\n", 'WATTSHIFT_POWER {"samples": 0}\n'])
def test_parse_power_says_none_when_there_is_no_usable_reading(log):
    assert parse_power(log) is None


def test_measure_downloads_the_kernel_log_and_parses_it():
    """`kaggle kernels output` writes <slug>.log: a JSON list of {stream_name, time, data} chunks."""
    chunks = [{"stream_name": "stdout", "time": 1.0, "data": "device cuda\n"}, {"stream_name": "stdout", "time": 2.0, "data": LINE + "\n"}]

    def fake_run(cmd, **kw):
        assert cmd[:3] == ["kaggle-bin", "kernels", "output"] and cmd[3] == "someone/slug"
        (Path(cmd[cmd.index("-p") + 1]) / "slug.log").write_text(json.dumps(chunks), encoding="utf-8")
        return subprocess.CompletedProcess([], 0, "Kernel log downloaded", "")

    p = KaggleProvider("someone", exe="kaggle-bin")
    with mock.patch("app.providers.kaggle.subprocess.run", fake_run):
        m = p.measure("someone/slug")
    assert m["avg_watts"] == 67.6 and m["gpu_model"] == "Tesla T4"


def test_the_twin_gets_its_own_kernel_slug_so_it_cannot_collide_with_the_real_run():
    seen = []

    def fake_run(cmd, **kw):
        seen.append(json.loads((Path(cmd[cmd.index("-p") + 1]) / "kernel-metadata.json").read_text())["id"])
        return subprocess.CompletedProcess([], 0, "pushed", "")

    p = KaggleProvider("someone", exe="kaggle-bin")
    job = Job(id=uuid.UUID("a3daf4ba-a341-4d28-b20d-efb2a7d03b41"), duration_minutes=60)
    with mock.patch("app.providers.kaggle.subprocess.run", fake_run):
        real, twin = p.start(job), p.start(job, tag="without")
    assert real == "someone/wattshift-a3daf4baa3" and twin == "someone/wattshift-a3daf4baa3-without" and seen == [real, twin]


def test_the_kernel_script_samples_power_and_prints_the_summary_line():
    src = (Path(__file__).resolve().parents[1] / "kaggle_kernel" / "wattshift_job.py").read_text()
    assert "power.draw" in src and "WATTSHIFT_POWER" in src


# --- the twin's life --------------------------------------------------------------------------------------------


def submit(session, provider="kaggle"):
    seed_tod(session)
    add_prices(session, NOW, evening_to_next_noon())
    return service.submit_job(session, 15, NOW + timedelta(hours=17), provider, now=NOW)


def runs(session, job):
    session.expire_all()
    return {r.kind: r for r in session.query(JobRun).filter(JobRun.job_id == job.id)}


def test_a_kaggle_job_gets_a_without_twin_at_submission(session):
    job = submit(session)
    assert runs(session, job)["without"].status == "scheduled"


def test_a_job_that_is_not_on_kaggle_gets_no_twin(session):
    job = submit(session, provider="github_actions")
    assert runs(session, job) == {}


def test_the_twin_fires_at_once_and_its_measurement_is_stored_when_it_finishes(session):
    fake = FakeProvider()
    job = submit(session)
    assert dispatch.fire_twins(session, {"kaggle": fake}, now=NOW) == 1
    twin = runs(session, job)["without"]
    assert twin.status == "running" and twin.started_at == NOW and twin.external_ref == f"fake/{job.id}-without"

    dispatch.poll_twins(session, {"kaggle": fake})  # still running: nothing stored
    assert runs(session, job)["without"].avg_watts is None

    fake.statuses[twin.external_ref] = "done"
    dispatch.poll_twins(session, {"kaggle": fake})
    twin = runs(session, job)["without"]
    assert twin.status == "done" and float(twin.avg_watts) == 68.0 and twin.gpu_model == "Tesla T4" and twin.samples == 30


def test_a_second_tick_does_not_fire_the_twin_again(session):
    fake = FakeProvider()
    submit(session)
    dispatch.fire_twins(session, {"kaggle": fake}, now=NOW)
    assert dispatch.fire_twins(session, {"kaggle": fake}, now=NOW + timedelta(seconds=5)) == 0
    assert len(fake.started) == 1


def test_twins_share_the_cap_on_runs_at_once_with_real_jobs(session):
    """Kaggle allows about two GPU sessions at once; a twin must not take a third."""
    fake = FakeProvider()
    submit(session)
    busy = Job(duration_minutes=5, deadline=NOW + timedelta(hours=2), provider="kaggle", status="running", assigned_window_start=NOW)
    session.add_all([busy, Job(duration_minutes=5, deadline=NOW + timedelta(hours=2), provider="kaggle", status="running", assigned_window_start=NOW)])
    session.flush()
    assert dispatch.fire_twins(session, {"kaggle": fake}, now=NOW) == 0  # two jobs already running
    assert fake.started == []


def test_a_running_twin_holds_a_slot_against_due_jobs(session):
    fake = FakeProvider()
    job = submit(session)
    dispatch.fire_twins(session, {"kaggle": fake}, now=NOW)
    for i in range(2):
        session.add(Job(duration_minutes=5, deadline=NOW + timedelta(hours=2), provider="kaggle", status="scheduled", assigned_window_start=NOW))
    session.flush()
    fired = dispatch.fire_due_jobs(session, {"kaggle": fake}, now=NOW)
    assert len(fired) == 1  # one twin running + one job = the cap of 2
    assert runs(session, job)["without"].status == "running"


def test_a_failed_twin_is_recorded_and_the_job_is_unaffected(session):
    fake = FakeProvider()
    job = submit(session)
    dispatch.fire_twins(session, {"kaggle": fake}, now=NOW)
    ref = runs(session, job)["without"].external_ref
    fake.statuses[ref] = "failed"
    dispatch.poll_twins(session, {"kaggle": fake})
    session.expire_all()
    assert runs(session, job)["without"].status == "failed"
    assert session.get(Job, job.id).status == "scheduled"


def test_a_twin_whose_kernel_cannot_start_is_recorded_as_failed(session):
    a = submit(session)
    fake = FakeProvider(fail_start=True)
    dispatch.fire_twins(session, {"kaggle": fake}, now=NOW)
    r = runs(session, a)["without"]
    assert r.status == "failed" and "kaggle exploded" in r.failure_reason


def test_a_finished_real_run_stores_its_own_measurement_as_the_with_run(session):
    fake = FakeProvider()
    job = submit(session)
    job.status, job.executed_at, job.external_ref = "running", SOLAR, f"fake/{job.id}"
    session.flush()
    fake.statuses[job.external_ref] = "done"
    dispatch.poll_running(session, {"kaggle": fake})
    with_run = runs(session, job)["with"]
    assert with_run.status == "done" and with_run.started_at == SOLAR and float(with_run.avg_watts) == 68.0


def test_a_job_still_finishes_when_the_measurement_cannot_be_read(session):
    """Losing the power reading must never lose the job's result or its savings row."""
    fake = FakeProvider()
    job = submit(session)
    job.status, job.executed_at, job.external_ref = "running", SOLAR, f"fake/{job.id}"
    session.flush()
    fake.statuses[job.external_ref] = "done"
    fake.measurements[job.external_ref] = RuntimeError("log not downloadable")
    dispatch.poll_running(session, {"kaggle": fake})
    session.expire_all()
    assert session.get(Job, job.id).status == "done"
    assert runs(session, job)["with"].avg_watts is None


# --- pricing what was measured ----------------------------------------------------------------------------------


def run(session, job, kind, started, watts, status="done"):
    session.add(JobRun(job_id=job.id, kind=kind, status=status, started_at=started, avg_watts=watts, peak_watts=watts + 2, energy_wh=watts / 120,
                       samples=30, gpu_model="Tesla T4", power_limit_w=70))
    session.flush()


def kaggle_job(session, submitted=NOW, status="done"):
    j = Job(id=uuid.uuid4(), duration_minutes=60, deadline=NOW + timedelta(hours=20), provider="kaggle", status=status,
            submitted_at=submitted, assigned_window_start=SOLAR, executed_at=SOLAR)
    session.add(j)
    session.flush()
    return j


def test_cost_per_gpu_hour_is_measured_watts_times_the_tariff_of_the_start_hour(session):
    seed_tod(session)
    j = kaggle_job(session)
    run(session, j, "without", PEAK, 68.0)
    run(session, j, "with", SOLAR, 67.0)
    out = measured.build(session, NOW)
    pair = out["jobs"][0]
    assert pair["complete"] is True
    assert pair["without"]["rs_per_gpu_hour"] == pytest.approx(0.068 * BASE * PEAK_MULT, abs=1e-4)
    assert pair["with"]["rs_per_gpu_hour"] == pytest.approx(0.067 * BASE * 0.85, abs=1e-4)
    assert pair["without"]["zone"] == "peak" and pair["with"]["zone"] == "solar"
    assert pair["saved_rs_per_gpu_hour"] == pytest.approx(0.068 * BASE * PEAK_MULT - 0.067 * BASE * 0.85, abs=1e-4)


def test_the_summary_averages_only_complete_pairs_and_keeps_a_negative_saving(session):
    seed_tod(session)
    good = kaggle_job(session)
    run(session, good, "without", PEAK, 68.0)
    run(session, good, "with", SOLAR, 68.0)
    worse = kaggle_job(session, submitted=NOW + timedelta(minutes=1))  # ran later at the SAME price: nothing saved
    run(session, worse, "without", SOLAR, 68.0)
    run(session, worse, "with", SOLAR, 68.0)
    waiting = kaggle_job(session, submitted=NOW + timedelta(minutes=2), status="scheduled")
    run(session, waiting, "without", PEAK, 68.0)  # its real run has not happened yet
    out = measured.build(session, NOW)
    s = out["summary"]
    assert s["pairs"] == 2 and s["waiting"] == 1
    without = 0.068 * BASE * PEAK_MULT + 0.068 * BASE * 0.85
    withw = 2 * 0.068 * BASE * 0.85
    assert s["rs_per_gpu_hour_without"] == pytest.approx(without / 2, abs=1e-4)
    assert s["rs_per_gpu_hour_with"] == pytest.approx(withw / 2, abs=1e-4)
    assert s["pct_saved"] == pytest.approx((without - withw) / without * 100, abs=0.01)
    pending = next(j for j in out["jobs"] if j["job_id"] == str(waiting.id))
    assert pending["complete"] is False and pending["with"]["status"] == "scheduled"


def test_a_run_without_a_reading_is_not_priced_and_the_pair_is_incomplete(session):
    seed_tod(session)
    j = kaggle_job(session)
    run(session, j, "without", PEAK, 68.0)
    session.add(JobRun(job_id=j.id, kind="with", status="done", started_at=SOLAR))  # measurement lost
    session.flush()
    out = measured.build(session, NOW)
    assert out["jobs"][0]["complete"] is False and out["jobs"][0]["with"]["rs_per_gpu_hour"] is None
    assert out["summary"]["pairs"] == 0 and out["summary"]["pct_saved"] is None


def test_the_basis_names_the_gpu_the_tariff_and_the_unit(session):
    seed_tod(session)
    j = kaggle_job(session)
    run(session, j, "without", PEAK, 68.0)
    run(session, j, "with", SOLAR, 68.0)
    b = measured.build(session, NOW)["basis"]
    assert b["gpu"] == "Tesla T4" and b["power_limit_w"] == 70 and b["per"] == "GPU-hour" and b["base_rate_rs_kwh"] == BASE
    assert "Maharashtra" in b["tariff"]


def test_with_nothing_measured_yet_the_answer_is_empty_not_an_error(session):
    seed_tod(session)
    out = measured.build(session, NOW)
    assert out["jobs"] == [] and out["summary"]["pairs"] == 0 and out["summary"]["pct_saved"] is None


# --- the endpoint -----------------------------------------------------------------------------------------------


def test_the_measured_endpoint_serves_the_same_answer(session):
    seed_tod(session)
    j = kaggle_job(session)
    run(session, j, "without", PEAK, 68.0)
    run(session, j, "with", SOLAR, 68.0)
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: NOW
    try:
        body = TestClient(main.app).get("/measured").json()
    finally:
        main.app.dependency_overrides.clear()
    assert body["summary"]["pairs"] == 1 and body["jobs"][0]["complete"] is True


# --- runs made on the replay clock are marked as such ------------------------------------------------------------


@pytest.fixture()
def replay_clock():
    from app import clock

    clock.start_sim(NOW, 60)
    yield
    clock.reset()


def test_runs_made_on_the_replay_clock_are_marked_simulated(session, replay_clock):
    job = submit(session)
    assert runs(session, job)["without"].simulated is True
    fake = FakeProvider()
    job.status, job.executed_at, job.external_ref = "running", SOLAR, f"fake/{job.id}"
    session.flush()
    fake.statuses[job.external_ref] = "done"
    dispatch.poll_running(session, {"kaggle": fake})
    assert runs(session, job)["with"].simulated is True


def test_runs_on_the_real_clock_are_not_marked(session):
    assert runs(session, submit(session))["without"].simulated is False


def test_the_summary_counts_the_pairs_that_ran_on_the_replay_clock(session):
    seed_tod(session)
    real, replayed = kaggle_job(session), kaggle_job(session, submitted=NOW + timedelta(minutes=1))
    run(session, real, "without", PEAK, 68.0)
    run(session, real, "with", SOLAR, 68.0)
    run(session, replayed, "without", PEAK, 68.0)
    run(session, replayed, "with", SOLAR, 68.0)
    for r in session.query(JobRun).filter(JobRun.job_id == replayed.id):
        r.simulated = True
    session.flush()
    out = measured.build(session, NOW)
    assert out["summary"]["pairs"] == 2 and out["summary"]["simulated_pairs"] == 1
    flags = {j["job_id"]: j["simulated"] for j in out["jobs"]}
    assert flags == {str(real.id): False, str(replayed.id): True}
