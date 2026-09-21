import time
from datetime import timedelta

import pytest
from sqlalchemy import delete, update

from app import db, dispatch, service
from app.allocator import overlaps
from app.config import settings
from app.models import Job, JobRun, PriceSignal
from app.savings import bill_cost
from app.seed import load_rules, seed_tod
from tests.helpers import NOW, STEP, add_prices, evening_to_next_noon

DEADLINE = NOW + timedelta(hours=17)  # next day 12:00 IST
CAP = 15


def submit(s, dur=15, **kw):
    return service.submit_job(s, dur, DEADLINE, "kaggle", now=NOW, cap_per_block=CAP, **kw)


def replan(s, now=NOW, **kw):
    return service.replan_pending(s, now=now, cap_per_block=CAP, **kw)


def reload(s, j):
    s.expire_all()
    return s.get(Job, j.id)


def set_price(s, idx, rate):
    s.execute(update(PriceSignal).where(PriceSignal.ts == NOW + idx * STEP).values(price_rs_per_mwh=rate))
    s.expire_all()


def load(s):
    out = {}
    for j in s.query(Job).filter(Job.status.in_(("scheduled", "running"))):
        for b, m in overlaps(j.assigned_window_start, j.duration_minutes):
            out[b] = out.get(b, 0) + m
    return out


@pytest.fixture()
def world(session):
    seed_tod(session)
    add_prices(session, NOW, evening_to_next_noon())  # cheap window at idx 60-63 (10:00-11:00 next day)
    return session


def test_unchanged_prices_change_nothing(world):
    # Real requests arrive at distinct instants; identical submitted_at values would fall back to id order.
    jobs = [
        service.submit_job(world, 15, DEADLINE, "kaggle", now=NOW + timedelta(seconds=i), cap_per_block=CAP)
        for i in range(3)
    ]
    before = {j.id: j.assigned_window_start for j in jobs}
    r = replan(world)
    assert r["moved"] == 0 and r["placed"] == 0
    assert {j.id: reload(world, j).assigned_window_start for j in jobs} == before  # deterministic, no churn


def test_a_cheaper_window_pulls_a_job_and_updates_its_planned_cost(world):
    j = submit(world)
    old = j.assigned_window_start
    for i in range(64, 68):  # 11:00-12:00 next day becomes much cheaper than the 10:00 window
        set_price(world, i, 1000.0)
    r = replan(world)
    j = reload(world, j)
    assert r["moved"] == 1
    assert j.assigned_window_start != old and j.assigned_window_start >= NOW + 64 * STEP
    expected = bill_cost(j.assigned_window_start, 15, j.power_kw, load_rules(world), settings.base_rate_rs_kwh)
    assert float(j.planned_cost_rs) == pytest.approx(expected)  # planned cost follows the new window
    assert float(j.baseline_cost_rs) == pytest.approx(bill_cost(NOW, 15, j.power_kw, load_rules(world), settings.base_rate_rs_kwh))


def test_a_job_about_to_fire_is_frozen(world):
    j = submit(world)
    old = j.assigned_window_start
    for i in range(64, 68):
        set_price(world, i, 1000.0)
    almost_due = old - timedelta(minutes=5)  # inside the 10-minute freeze
    r = replan(world, now=almost_due, freeze_minutes=10)
    assert r["moved"] == 0
    assert reload(world, j).assigned_window_start == old


def test_a_queued_job_is_placed_once_prices_arrive(session):
    """The real reason F9 exists: tomorrow's prices are not published when the job is submitted."""
    seed_tod(session)
    j = submit(session)
    assert j.status == "queued" and j.assigned_window_start is None
    add_prices(session, NOW, evening_to_next_noon())
    r = replan(session)
    j = reload(session, j)
    assert r["placed"] == 1
    assert j.status == "scheduled" and j.assigned_window_start is not None
    assert float(j.planned_cost_rs) < float(j.baseline_cost_rs)


def test_running_jobs_keep_their_capacity_booked(world):
    """A running job's blocks stay in the ledger, so a re-planned job cannot pile onto them."""
    a = submit(world)
    a.status = "running"
    world.flush()
    b = submit(world)
    world.flush()
    replan(world)
    assert max(load(world).values()) <= CAP
    assert reload(world, a).status == "running"  # never touched


def test_capacity_is_respected_after_replanning_many_jobs(world):
    for _ in range(4):
        submit(world)
    for i in range(64, 68):
        set_price(world, i, 1000.0)
    replan(world)
    assert max(load(world).values()) <= CAP


def test_a_scheduled_job_that_cannot_be_replaced_keeps_its_window(world):
    j = submit(world)
    old = j.assigned_window_start
    world.execute(delete(PriceSignal))  # a broken feed: no prices at all
    world.expire_all()
    r = replan(world)
    j = reload(world, j)
    assert j.status == "scheduled" and j.assigned_window_start == old  # never demoted by a bad refresh
    assert r["moved"] == 0


def test_a_queued_job_with_nothing_to_place_stays_queued(session):
    seed_tod(session)
    j = submit(session)
    r = replan(session)
    assert reload(session, j).status == "queued" and r["still_queued"] == 1


def test_queued_jobs_past_their_deadline_are_failed_not_left_forever(session):
    seed_tod(session)
    j = submit(session)  # queued: no prices
    dispatch.expire_overdue(session, DEADLINE + timedelta(minutes=1))
    j = reload(session, j)
    assert j.status == "failed" and "deadline" in j.failure_reason


def test_replan_never_touches_terminal_jobs(world):
    done = submit(world)
    done.status = "done"
    failed = submit(world)
    failed.status = "failed"
    world.flush()
    win = (done.assigned_window_start, failed.assigned_window_start)
    replan(world)
    assert (reload(world, done).assigned_window_start, reload(world, failed).assigned_window_start) == win


def test_runner_replans_on_a_timer(engine):
    """Integration: real APScheduler places a queued job once prices exist, with no request involved."""
    from app import runner
    from app.providers.fake import FakeProvider

    factory = db.make_session_factory(engine)
    with factory() as s, s.begin():
        seed_tod(s)
        job = service.submit_job(s, 15, DEADLINE, "kaggle", now=NOW, cap_per_block=CAP)
        assert job.status == "queued"
        job_id = job.id
        add_prices(s, NOW, evening_to_next_noon())
    sched = runner.start(factory, {"kaggle": FakeProvider()}, dispatch_seconds=60, ingest=False, replan_seconds=1, now=lambda: NOW)
    try:
        status = None
        t0 = time.time()
        while time.time() - t0 < 15:
            with factory() as s:
                status = s.get(Job, job_id).status
            if status == "scheduled":
                break
            time.sleep(0.4)
        assert status == "scheduled"
    finally:
        sched.shutdown(wait=False)
        with factory() as s, s.begin():
            s.execute(delete(JobRun))
            s.execute(delete(Job))
            s.execute(delete(PriceSignal))


def test_a_job_waits_for_prices_it_does_not_have_instead_of_starting_now(session):
    """The Phase 7 gap: only tonight's (dear) prices are published, so the best KNOWN window is right now, which no later
    price update could move. The job stays queued until the rest of its window is priced, then takes the cheap window."""
    seed_tod(session)
    add_prices(session, NOW, [10000.0] * 8)  # 19:00-21:00 known, everything else not published yet
    j = submit(session)
    assert j.status == "queued" and j.assigned_window_start is None
    assert replan(session, now=NOW + timedelta(minutes=5))["still_queued"] == 1  # a re-plan alone does not start it either
    add_prices(session, NOW + 8 * STEP, evening_to_next_noon()[8:])  # tomorrow's prices arrive
    assert replan(session, now=NOW + timedelta(minutes=10))["placed"] == 1
    j = reload(session, j)
    assert j.status == "scheduled" and j.assigned_window_start >= NOW + 60 * STEP  # the solar window, not tonight


def test_a_waiting_job_takes_the_best_known_window_once_the_priced_horizon_runs_short(session):
    """Waiting must not run the job out of the windows it has: when the known prices are about to run out, it takes the
    best known window even though the rest of the deadline is still unpriced."""
    seed_tod(session)
    deadline = NOW + timedelta(hours=17)
    first = deadline - timedelta(minutes=135)
    add_prices(session, first, [10000.0] * 8)  # priced from 135 to 15 minutes before the deadline
    j = service.submit_job(session, 15, deadline, "kaggle", now=first, cap_per_block=CAP)
    assert j.status == "queued"
    assert service.replan_pending(session, now=deadline - timedelta(minutes=90), cap_per_block=CAP)["still_queued"] == 1
    r = service.replan_pending(session, now=deadline - timedelta(minutes=60), cap_per_block=CAP)
    assert r["placed"] == 1 and reload(session, j).status == "scheduled"


def test_fully_priced_jobs_are_placed_at_once(world):
    """The rule only applies while prices are missing: with the whole window priced nothing waits."""
    assert submit(world).status == "scheduled"
