import uuid
from datetime import date, timedelta

import pytest

from app import planner, sites
from app.allocator import overlaps
from app.catalogue import get_tariff, seed_catalogue
from app.models import AuditLog, Decision, ManagedJob, PriceSignal
from app.seed import seed_tod
from tests.helpers import NOW, add_prices, evening_to_next_noon

CHEAP_START = NOW + timedelta(hours=15)  # 10:00 IST next day; the 4 cheap blocks run to 11:00
CHEAP_END = NOW + timedelta(hours=16)
MIN = timedelta(minutes=1)


@pytest.fixture()
def world(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon())
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)  # cap = 0.5 * 8 * 15 = 60 GPU-minutes per block
    tariff = get_tariff(session, "MSEDCL", "HT-I(A)", date(2026, 7, 1))
    return session, site, tariff


def add_job(session, site, ref, *, gpus=4, limit=30, wait=17 * 60, state="PENDING", first_seen=NOW, **kw):
    j = ManagedJob(
        id=uuid.uuid4(), site_id=site.id, ref=ref, state=state, first_seen=first_seen, submit_time=first_seen,
        gpus=gpus, time_limit_min=limit, max_wait_min=wait, **kw,
    )
    session.add(j)
    session.flush()
    return j


def plan(world, now=NOW, mode="autonomous"):
    session, site, tariff = world
    return planner.plan_site(session, site, tariff, now=now, mode=mode)


# --- baseline guard ---------------------------------------------------------------------------------------------

def test_a_prediction_a_year_out_is_ignored():
    assert planner.plausible_predicted(NOW + timedelta(days=365), NOW, 1440) is None


def test_missing_prediction_or_missing_max_wait_is_ignored():
    assert planner.plausible_predicted(None, NOW, 1440) is None
    assert planner.plausible_predicted(NOW + timedelta(hours=1), NOW, None) is None


def test_a_prediction_inside_the_wait_is_accepted():
    p = NOW + timedelta(hours=2)
    assert planner.plausible_predicted(p, NOW, 1440) == p


def test_baseline_falls_back_to_first_seen_and_never_precedes_it():
    j = ManagedJob(first_seen=NOW, max_wait_min=1440, predicted_start=NOW + timedelta(days=365))
    assert planner.baseline_of(j) == NOW
    j.predicted_start = NOW - timedelta(hours=1)
    assert planner.baseline_of(j) == NOW
    j.predicted_start = NOW + timedelta(hours=3)
    assert planner.baseline_of(j) == NOW + timedelta(hours=3)


def test_site_cap_uses_share_gpus_and_the_power_limit(session):
    a, _ = sites.create_site(session, "A", "s", gpus=8)
    assert planner.site_cap(a) == 60
    b, _ = sites.create_site(session, "B", "s", gpus=8, power_limit_kw=5, kw_per_gpu=1.25)  # 4 GPUs of power
    assert planner.site_cap(b) == 60  # min(60, 5 / 1.25 * 15 = 60)
    c, _ = sites.create_site(session, "C", "s", gpus=8, power_limit_kw=2.5, kw_per_gpu=1.25)  # 2 GPUs of power
    assert planner.site_cap(c) == 30


# --- planning ---------------------------------------------------------------------------------------------------

def test_defers_a_flexible_job_into_the_cheap_window(world):
    session, site, _ = world
    j = add_job(session, site, "1")
    stats = plan(world)
    assert stats["deferred"] == 1
    assert j.plan_status == "planned"
    assert CHEAP_START <= j.planned_start and j.planned_start + 30 * MIN <= CHEAP_END
    assert j.baseline_start == NOW
    assert float(j.planned_cost) < float(j.baseline_cost)
    d = session.query(Decision).one()
    assert d.start_at == j.planned_start and d.mode == "autonomous"


def test_never_later_than_baseline_plus_wait_minus_margin(world):
    session, site, _ = world
    site.start_margin_s = 3600  # a large margin makes the rule visible: latest start = NOW + 14 h - 1 h = 08:00 next day
    j = add_job(session, site, "1", wait=14 * 60)  # the cheap window (10:00) is out of reach
    plan(world)
    assert j.planned_start is None or j.planned_start <= NOW + timedelta(hours=13)


def test_a_predicted_baseline_moves_the_window(world):
    session, site, _ = world
    j = add_job(session, site, "1", predicted_start=NOW + timedelta(hours=2))
    plan(world)
    assert j.baseline_start == NOW + timedelta(hours=2)
    assert j.planned_start >= NOW + timedelta(hours=2)


def test_runs_as_normal_when_now_is_already_the_cheapest_bill(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon(cheap_from_idx=0))  # cheap right now; the bill is peak either way
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    tariff = get_tariff(session, "MSEDCL", "HT-I(A)", date(2026, 7, 1))
    j = add_job(session, site, "1")
    stats = planner.plan_site(session, site, tariff, now=NOW, mode="autonomous")
    assert stats["normal"] == 1 and stats["deferred"] == 0
    assert j.plan_status == "planned" and j.planned_start is None
    assert float(j.planned_cost) == pytest.approx(float(j.baseline_cost))
    assert session.query(Decision).count() == 0


def test_capacity_spreads_jobs_and_never_overbooks(world):
    session, site, _ = world
    jobs = [add_job(session, site, str(i), gpus=4, limit=15) for i in range(3)]  # 4 GPUs x 15 min = 60 = one full block
    plan(world)
    load: dict = {}
    for j in jobs:
        assert j.planned_start is not None
        for b, m in overlaps(j.planned_start, 15):
            load[b] = load.get(b, 0) + m * 4
    assert max(load.values()) <= planner.site_cap(site)
    assert len({j.planned_start for j in jobs}) == 3


def test_replanning_unchanged_prices_changes_nothing(world):
    session, site, _ = world
    jobs = [add_job(session, site, str(i), gpus=2, limit=20) for i in range(3)]
    plan(world)
    before = [j.planned_start for j in jobs]
    n_decisions = session.query(Decision).count()
    stats = plan(world, now=NOW + timedelta(seconds=30))
    assert [j.planned_start for j in jobs] == before
    assert stats["deferred"] == 0 and stats["unchanged"] == 3
    assert session.query(Decision).count() == n_decisions


def test_a_job_about_to_start_is_pinned(world):
    session, site, _ = world
    soon = NOW + timedelta(minutes=5)  # inside the 10-minute freeze margin
    j = add_job(session, site, "1", plan_status="planned", planned_start=soon, applied_start=soon)
    plan(world)
    assert j.planned_start == soon and session.query(Decision).count() == 0


def test_running_deferred_jobs_keep_their_capacity(world):
    session, site, _ = world
    add_job(  # 4 GPUs for 60 min from 10:00 = the whole cheap window at 60 GPU-min per block
        session, site, "run", gpus=4, limit=60, state="RUNNING", actual_start=CHEAP_START, applied_start=CHEAP_START,
        plan_status="planned",
    )
    j = add_job(session, site, "2", gpus=4, limit=15)
    plan(world)
    assert j.planned_start is not None
    assert j.planned_start + 15 * MIN <= CHEAP_START or j.planned_start >= CHEAP_END
    assert float(j.planned_cost) < float(j.baseline_cost)  # still a solar-zone hour, just not the cheap IEX one


def test_no_prices_means_unplaceable_and_then_placed_when_prices_arrive(session):
    seed_tod(session)
    seed_catalogue(session)
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    tariff = get_tariff(session, "MSEDCL", "HT-I(A)", date(2026, 7, 1))
    j = add_job(session, site, "1")
    stats = planner.plan_site(session, site, tariff, now=NOW, mode="autonomous")
    assert stats["unplaceable"] == 1 and j.plan_status == "unplaceable"
    add_prices(session, NOW, evening_to_next_noon())
    planner.plan_site(session, site, tariff, now=NOW + 10 * MIN, mode="autonomous")
    assert j.plan_status == "planned" and j.planned_start is not None


def test_a_job_bigger_than_the_site_cap_is_unplaceable(world):
    session, site, _ = world
    j = add_job(session, site, "1", gpus=100)
    plan(world)
    assert j.plan_status == "unplaceable" and j.planned_start is None


def test_a_job_without_a_time_limit_is_unplaceable(world):
    session, site, _ = world
    j = add_job(session, site, "1", limit=None)
    plan(world)
    assert j.plan_status == "unplaceable"
    assert session.query(AuditLog).filter_by(event="unplaceable", ref="1").count() == 1


@pytest.mark.parametrize("status", ["skipped", "abandoned", "released"])
def test_jobs_we_stopped_managing_are_never_touched(world, status):
    session, site, _ = world
    j = add_job(session, site, "1", plan_status=status)
    plan(world)
    assert j.plan_status == status and j.planned_start is None and j.baseline_start is None


def test_a_planned_job_keeps_its_window_if_the_prices_disappear(world):
    session, site, tariff = world
    j = add_job(session, site, "1")
    plan(world)
    kept = j.planned_start
    session.query(PriceSignal).delete()
    session.flush()
    stats = plan(world, now=NOW + 10 * MIN)
    assert j.plan_status == "planned" and j.planned_start == kept and stats["unchanged"] == 1
