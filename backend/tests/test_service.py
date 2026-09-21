from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import delete

from app import db, service
from app.allocator import overlaps
from app.config import settings
from app.models import Job, JobRun, PriceSignal
from app.seed import seed_tod
from tests.helpers import NOW, PEAK_MULT, STEP, add_prices, evening_to_next_noon

DEADLINE = NOW + timedelta(hours=17)  # next day 12:00 IST
CHEAP_START = NOW + 60 * STEP  # next day 10:00 IST
CHEAP_END = CHEAP_START + 4 * STEP  # 11:00


@pytest.fixture()
def world(session):
    seed_tod(session)
    add_prices(session, NOW, evening_to_next_noon())
    return session


def submit(s, dur=15, deadline=DEADLINE, provider="kaggle", cap=15, **kw):
    return service.submit_job(s, dur, deadline, provider, now=NOW, cap_per_block=cap, **kw)


def per_block_load(s):
    load = {}
    for j in s.query(Job).filter(Job.status.in_(("scheduled", "running"))):
        for b, m in overlaps(j.assigned_window_start, j.duration_minutes):
            load[b] = load.get(b, 0) + m
    return load


def test_job_is_held_for_the_cheap_solar_window(world):
    j = submit(world)
    assert j.status == "scheduled"
    assert CHEAP_START <= j.assigned_window_start < CHEAP_END  # not "now"
    assert j.assigned_window_start + timedelta(minutes=15) <= DEADLINE
    assert j.planned_cost_rs < j.baseline_cost_rs  # solar 0.85 vs the peak multiplier
    assert float(j.power_kw) == settings.default_power_kw


def test_baseline_is_run_now_cost(world):
    j = submit(world, dur=30, power_kw=10)
    # 30 min at 19:00 IST (peak), 10 kW, base rate from settings
    assert float(j.baseline_cost_rs) == pytest.approx(0.5 * 10 * settings.base_rate_rs_kwh * PEAK_MULT)


def test_capacity_cap_spreads_jobs_across_windows(world):
    """Success criterion 3: jobs land in different windows because of the cap, and no block is overbooked."""
    jobs = [submit(world) for _ in range(3)]
    assert all(j.status == "scheduled" for j in jobs)
    assert len({j.assigned_window_start for j in jobs}) == 3
    assert max(per_block_load(world).values()) <= 15


def test_no_capacity_stores_job_as_queued(world):
    jobs = [submit(world, deadline=NOW + 2 * STEP + timedelta(minutes=15)) for _ in range(6)]
    queued = [j for j in jobs if j.status == "queued"]
    assert queued, "6 jobs cannot fit in 3 blocks at cap 15"
    assert all(j.assigned_window_start is None and j.planned_cost_rs is None for j in queued)
    assert all(j.baseline_cost_rs is not None for j in queued)  # still recorded, never silently dropped
    assert max(per_block_load(world).values()) <= 15


def test_no_prices_before_deadline_queues(session):
    seed_tod(session)  # no price rows at all
    j = submit(session)
    assert j.status == "queued" and j.assigned_window_start is None


@pytest.mark.parametrize(
    "kw",
    [
        {"dur": 0},
        {"dur": 181},
        {"provider": "runpod"},
        {"deadline": NOW + timedelta(minutes=10), "dur": 30},  # cannot finish in time
        {"deadline": NOW.replace(tzinfo=None) + timedelta(hours=5)},  # naive datetime
        {"power_kw": 0},
    ],
)
def test_validation_rejects_bad_input(world, kw):
    with pytest.raises(ValueError):
        submit(world, **kw)
    assert world.query(Job).count() == 0


def test_replay_prices_only_used_in_sim_mode(session):
    seed_tod(session)
    add_prices(session, NOW, evening_to_next_noon(), source="iex_dam_replay")
    assert submit(session).status == "queued"  # live mode ignores replay rows


def test_concurrent_submits_never_oversubscribe_a_block(engine):
    """Advisory lock: 8 simultaneous submits into a tiny cheap window must respect the cap."""
    factory = db.make_session_factory(engine)
    with factory() as s, s.begin():
        seed_tod(s)
        add_prices(s, NOW, evening_to_next_noon(cheap_from_idx=4, cheap_blocks=8))  # 20:00-22:00 cheap

    def go(_):
        with factory() as s, s.begin():
            return service.submit_job(s, 15, DEADLINE, "kaggle", now=NOW, cap_per_block=15).id

    try:
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(go, range(8)))
        with factory() as s:
            load = per_block_load(s)
            assert max(load.values()) <= 15
            assert s.query(Job).filter_by(status="scheduled").count() == 8
    finally:
        with factory() as s, s.begin():
            s.execute(delete(JobRun))
            s.execute(delete(Job))
            s.execute(delete(PriceSignal))
