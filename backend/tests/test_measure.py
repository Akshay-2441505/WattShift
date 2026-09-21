import uuid
from datetime import timedelta

import pytest

from app import agent_sync, measure, sites
from app.catalogue import seed_catalogue
from app.models import AuditLog, ManagedJob
from app.seed import seed_tod
from tests.helpers import NOW, add_prices, evening_to_next_noon

SOLAR_START = NOW + timedelta(hours=17)  # Thu 2026-07-02 12:00 IST: solar, x0.85 in July. NOW is 19:00 IST: peak, x1.25.


@pytest.fixture()
def world(session):
    seed_tod(session)
    seed_catalogue(session)
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8, mode="autonomous")  # 1.25 kW per GPU
    return session, site


def job(session, site, **kw):
    base = dict(
        id=uuid.uuid4(), site_id=site.id, ref="1", state="COMPLETED", plan_status="planned", first_seen=NOW, submit_time=NOW,
        gpus=8, time_limit_min=90, max_wait_min=1440, baseline_start=NOW, applied_start=SOLAR_START,
        actual_start=SOLAR_START, actual_end=SOLAR_START + timedelta(minutes=60),
    )
    j = ManagedJob(**{**base, **kw})
    session.add(j)
    session.flush()
    return j


def test_the_saving_matches_a_hand_calculation(world):
    session, site = world
    j = job(session, site)  # 8 GPUs x 1.25 kW = 10 kW for 60 min
    assert measure.finalize(session, site, j, NOW) is True
    assert float(j.baseline_cost) == pytest.approx(10 * 8.44 * 1.25)  # 105.50 at peak
    assert float(j.actual_cost) == pytest.approx(10 * 8.44 * 0.85)  # 71.74 at solar
    assert float(j.saved) == pytest.approx(33.76)


def test_baseline_and_actual_use_the_real_runtime_not_the_time_limit(world):
    session, site = world
    j = job(session, site, actual_end=SOLAR_START + timedelta(minutes=30))  # limit says 90, it ran 30
    measure.finalize(session, site, j, NOW)
    assert float(j.actual_cost) == pytest.approx(5 * 8.44 * 0.85)
    assert float(j.baseline_cost) == pytest.approx(5 * 8.44 * 1.25)


def test_a_negative_saving_is_kept(world):
    session, site = world
    j = job(session, site, baseline_start=SOLAR_START, actual_start=NOW, actual_end=NOW + timedelta(minutes=60), applied_start=NOW)
    measure.finalize(session, site, j, NOW)
    assert float(j.saved) == pytest.approx(10 * 8.44 * (0.85 - 1.25))
    assert float(j.saved) < 0


@pytest.mark.parametrize(
    "kw",
    [
        {"applied_start": None},  # we never deferred it: nothing of ours to measure
        {"state": "RUNNING", "actual_end": None},
        {"actual_start": None},  # cancelled while pending
        {"baseline_start": None},
    ],
)
def test_jobs_we_cannot_honestly_measure_are_left_alone(world, kw):
    session, site = world
    j = job(session, site, **kw)
    assert measure.finalize(session, site, j, NOW) is False
    assert j.actual_cost is None and j.saved is None


def test_a_measurement_is_written_once(world):
    session, site = world
    j = job(session, site)
    assert measure.finalize(session, site, j, NOW) is True
    assert measure.finalize(session, site, j, NOW + timedelta(seconds=30)) is False
    assert session.query(AuditLog).filter_by(event="measured").count() == 1


def test_a_short_job_counts_at_least_one_minute(world):
    session, site = world
    assert measure.elapsed_minutes(job(session, site, actual_end=SOLAR_START + timedelta(seconds=5))) == 1


def test_a_finished_report_from_the_agent_is_priced_by_the_cloud_itself(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon())
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8, mode="autonomous")
    fact = {"ref": "9", "state": "PENDING", "submit_time": NOW.isoformat(), "gpus": 2, "time_limit_min": 60, "max_wait_min": 17 * 60}
    body = lambda **kw: agent_sync.SyncIn.model_validate({"agent_version": "0", "mode": "autonomous", "sent_at": NOW.isoformat(), **kw})  # noqa: E731
    out = agent_sync.process_sync(session, site, body(jobs=[fact]), NOW)
    start = out.decisions[0].start_at
    done = {**fact, "state": "COMPLETED", "start_time": start.isoformat(), "end_time": (start + timedelta(minutes=60)).isoformat()}
    agent_sync.process_sync(session, site, body(jobs=[done], applied=[{"ref": "9", "start_at": start.isoformat(), "ok": True}]), NOW + timedelta(hours=17))
    session.expire_all()
    j = session.query(ManagedJob).filter_by(ref="9").one()
    assert j.saved is not None and float(j.saved) > 0 and float(j.actual_cost) < float(j.baseline_cost)
