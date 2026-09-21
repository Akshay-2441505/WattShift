from datetime import timedelta

import pytest

from app import agent_sync, sites
from app.catalogue import seed_catalogue
from app.models import AuditLog, Decision, ManagedJob
from app.seed import seed_tod
from tests.helpers import NOW, add_prices, evening_to_next_noon

CHEAP_START = NOW + timedelta(hours=15)
CHEAP_END = NOW + timedelta(hours=16)


@pytest.fixture()
def world(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon())
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8, mode="autonomous")
    return session, site


def fact(ref, **kw):
    base = {"ref": ref, "state": "PENDING", "submit_time": NOW.isoformat(), "gpus": 4, "time_limit_min": 30, "max_wait_min": 17 * 60}
    return {**base, **kw}


def body(*jobs, mode="autonomous", **kw):
    return agent_sync.SyncIn.model_validate(
        {"agent_version": "0.1.0", "mode": mode, "sent_at": NOW.isoformat(), "jobs": list(jobs), **kw}
    )


def sync(world, b, now=NOW):
    session, site = world
    return agent_sync.process_sync(session, site, b, now)


def job(session, ref="1"):
    session.expire_all()
    return session.query(ManagedJob).filter_by(ref=ref).one()


def events(session):
    return [(a.actor, a.event) for a in session.query(AuditLog).order_by(AuditLog.id)]


def test_first_sync_records_the_job_and_returns_a_decision(world):
    session, _ = world
    out = sync(world, body(fact("1")))
    assert [d.ref for d in out.decisions] == ["1"]
    assert CHEAP_START <= out.decisions[0].start_at and out.decisions[0].start_at + timedelta(minutes=30) <= CHEAP_END
    assert out.release_all is False and out.next_poll_s == 30
    j = job(session)
    assert j.plan_status == "planned" and j.gpus == 4 and j.first_seen == NOW and j.baseline_start == NOW
    assert ("agent", "job_seen") in events(session) and ("planner", "decision") in events(session)


def test_shadow_plans_and_logs_but_returns_no_decisions(world):
    session, site = world
    site.mode = "shadow"
    session.flush()  # process_sync re-reads the site row after taking the lock
    out = sync(world, body(fact("1"), mode="shadow"))
    assert out.decisions == []
    d = session.query(Decision).one()
    assert d.mode == "shadow" and float(d.planned_cost) < float(d.baseline_cost)  # the "would have saved" figure


def test_both_sides_must_say_autonomous(world):
    session, site = world  # site says autonomous, the agent says shadow
    assert sync(world, body(fact("1"), mode="shadow")).decisions == []
    assert ("planner", "mode_mismatch") in events(session)
    site.mode = "shadow"  # ...and the other way round: the agent says autonomous, the site says shadow
    session.flush()
    assert sync(world, body(fact("2"), mode="autonomous"), NOW + timedelta(seconds=30)).decisions == []


def test_a_repeated_sync_changes_nothing(world):
    session, _ = world
    b = body(fact("1"), fact("2", gpus=2))
    first = sync(world, b)
    n_dec, n_ev, n_jobs = session.query(Decision).count(), len(events(session)), session.query(ManagedJob).count()
    second = sync(world, b, NOW + timedelta(seconds=30))
    assert second.decisions == first.decisions
    assert (session.query(Decision).count(), len(events(session)), session.query(ManagedJob).count()) == (n_dec, n_ev, n_jobs)


def test_a_confirmed_start_time_is_not_sent_again(world):
    session, _ = world
    start = sync(world, body(fact("1"))).decisions[0].start_at
    out = sync(world, body(fact("1"), applied=[{"ref": "1", "start_at": start.isoformat(), "ok": True, "error": None}]), NOW + timedelta(seconds=30))
    assert out.decisions == [] and job(session).applied_start == start


def test_an_apply_failure_abandons_the_job(world):
    session, _ = world
    start = sync(world, body(fact("1"))).decisions[0].start_at
    b = body(fact("1"), applied=[{"ref": "1", "start_at": start.isoformat(), "ok": False, "error": "Invalid user id"}])
    assert sync(world, b, NOW + timedelta(seconds=30)).decisions == []
    j = job(session)
    assert j.plan_status == "abandoned" and "Invalid user id" in j.note
    assert sync(world, body(fact("1")), NOW + timedelta(seconds=60)).decisions == []  # never retried


def test_a_user_override_stops_management(world):
    session, _ = world
    sync(world, body(fact("1")))
    assert sync(world, body(fact("1", override=True)), NOW + timedelta(seconds=30)).decisions == []
    assert job(session).plan_status == "abandoned" and job(session).note == "user_changed"


def test_skipped_jobs_are_never_planned(world):
    session, _ = world
    assert sync(world, body(fact("1", skipped_reason="array"))).decisions == []
    assert job(session).plan_status == "skipped"


def test_release_all_round_trip(world):
    session, site = world
    first = {d.ref: d.start_at for d in sync(world, body(fact("1"), fact("3", gpus=2))).decisions}
    assert set(first) == {"1", "3"}  # "3" is planned but the agent never confirms it
    sync(world, body(fact("1"), fact("3", gpus=2), applied=[{"ref": "1", "start_at": first["1"].isoformat(), "ok": True}]), NOW + timedelta(seconds=30))
    sites.set_release_all(session, site, True, NOW + timedelta(seconds=40))
    out = sync(world, body(fact("1"), fact("2"), fact("3", gpus=2)), NOW + timedelta(seconds=60))
    assert out.release_all is True and out.decisions == []  # no decision is sent, and nothing new is planned, while the switch is on
    assert job(session, "2").plan_status == "pending"
    sync(world, body(fact("1"), fact("2"), fact("3", gpus=2), released=["1"]), NOW + timedelta(seconds=90))
    assert job(session, "1").plan_status == "released"
    sites.set_release_all(session, site, False, NOW + timedelta(seconds=120))
    out = sync(world, body(fact("1"), fact("2"), fact("3", gpus=2)), NOW + timedelta(seconds=150))
    assert sorted(d.ref for d in out.decisions) == ["2", "3"]  # released "1" stays released; the others are planned again


def test_the_agent_can_ask_for_release_all(world):
    session, site = world
    out = sync(world, body(fact("1"), release_all=True))
    assert site.release_all is True and out.release_all is True and out.decisions == []


def test_states_never_go_backwards(world):
    session, _ = world
    sync(world, body(fact("1", state="RUNNING", start_time=(NOW + timedelta(minutes=1)).isoformat())))
    sync(world, body(fact("1", state="PENDING")), NOW + timedelta(seconds=30))
    assert job(session).state == "RUNNING"


def test_a_finished_job_records_its_real_times(world):
    session, _ = world
    s, e = NOW + timedelta(hours=1), NOW + timedelta(hours=2)
    sync(world, body(fact("1", state="COMPLETED", start_time=s.isoformat(), end_time=e.isoformat())))
    j = job(session)
    assert (j.state, j.actual_start, j.actual_end) == ("COMPLETED", s, e)


def test_gpus_are_captured_once(world):
    session, _ = world
    sync(world, body(fact("1", gpus=4)))
    sync(world, body(fact("1", gpus=99)), NOW + timedelta(seconds=30))
    assert job(session).gpus == 4


def test_a_late_plausible_prediction_updates_the_baseline_until_a_start_time_is_applied(world):
    session, _ = world
    sync(world, body(fact("1", predicted_start=(NOW + timedelta(days=365)).isoformat())))
    assert job(session).baseline_start == NOW  # bogus year-ahead value ignored
    sync(world, body(fact("1", predicted_start=(NOW + timedelta(hours=2)).isoformat())), NOW + timedelta(seconds=30))
    assert job(session).baseline_start == NOW + timedelta(hours=2)


def test_no_valid_tariff_plans_nothing_but_still_records_jobs(world):
    session, _ = world
    later = NOW.replace(year=2028)
    out = sync(world, body(fact("1")), later)
    assert out.decisions == [] and job(session).plan_status == "pending"
    assert ("planner", "no_tariff") in events(session)


def test_a_shadow_agent_reporting_writes_is_flagged(world):
    session, _ = world
    sync(world, body(fact("1"), mode="shadow", applied=[{"ref": "1", "start_at": NOW.isoformat(), "ok": True}]))
    assert ("agent", "shadow_violation") in events(session)
