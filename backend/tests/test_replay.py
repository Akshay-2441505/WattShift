from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import clock, main, replay, service
from app.models import Job, PriceSignal, SavingsLog
from app.seed import seed_tod
from app.tariff import IST
from tests.helpers import NOW, add_prices

TODAY = date(2026, 7, 1)


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    clock.reset()


def test_sample_day_is_a_real_96_block_day():
    rows = replay.load_sample_day()
    assert len(rows) == 96
    assert (rows[0][0], rows[0][1]) == (0, 0) and (rows[-1][0], rows[-1][1]) == (23, 45)
    prices = [p for _, _, p in rows]
    assert max(prices) == 10000.0 and min(prices) < 3000  # the cap, and a real solar-hours dip


def test_seed_replay_maps_the_day_onto_today_and_tomorrow_at_the_same_clock_time(session):
    anchor = replay.seed_replay(session, "19:00", today=TODAY)
    assert anchor == datetime(2026, 7, 1, 19, tzinfo=IST)
    rows = session.query(PriceSignal).filter_by(source="iex_dam_replay").order_by(PriceSignal.ts).all()
    assert len(rows) == 192
    assert rows[0].ts == datetime(2026, 7, 1, 0, 0, tzinfo=IST) and rows[-1].ts == datetime(2026, 7, 2, 23, 45, tzinfo=IST)
    sample = {(h, m): p for h, m, p in replay.load_sample_day()}
    for r in (rows[10], rows[100]):
        t = r.ts.astimezone(IST)
        assert float(r.price_rs_per_mwh) == sample[(t.hour, t.minute)]


def test_seed_replay_is_idempotent_and_leaves_live_prices_alone(session):
    add_prices(session, NOW, [7.0] * 4, source="iex_dam")
    replay.seed_replay(session, "19:00", today=TODAY)
    replay.seed_replay(session, "19:00", today=TODAY)
    assert session.query(PriceSignal).filter_by(source="iex_dam_replay").count() == 192
    assert session.query(PriceSignal).filter_by(source="iex_dam").count() == 4


def test_reset_jobs_clears_jobs_and_savings_only_when_asked(session):
    seed_tod(session)
    j = Job(duration_minutes=15, deadline=NOW + timedelta(hours=5), provider="kaggle", status="done",
            executed_at=NOW, baseline_cost_rs=10)
    session.add(j)
    session.flush()
    session.add(SavingsLog(job_id=j.id, baseline_cost_rs=10, actual_cost_rs=8, saved_rs=2))
    session.flush()
    replay.seed_replay(session, "19:00", today=TODAY, reset_jobs=False)
    assert session.query(Job).count() == 1
    replay.seed_replay(session, "19:00", today=TODAY, reset_jobs=True)
    assert session.query(Job).count() == 0 and session.query(SavingsLog).count() == 0


@pytest.mark.parametrize("bad", ["7pm", "25:00", "19:60", ""])
def test_seed_replay_rejects_bad_start_time(session, bad):
    with pytest.raises(ValueError):
        replay.seed_replay(session, bad, today=TODAY)


def test_replay_places_an_evening_job_in_the_solar_window_and_saves_money(session):
    """The demo's core claim, on real IEX prices: submit at 19:00 (peak), it is held for solar hours."""
    seed_tod(session)
    anchor = replay.seed_replay(session, "19:00", today=TODAY)
    clock.start_sim(anchor, scale=1)  # scale 1 so 'now' stays put during the test
    job = service.submit_job(session, 60, anchor + timedelta(hours=19), "kaggle", now=anchor, cap_per_block=30)
    assert job.status == "scheduled"
    start = job.assigned_window_start.astimezone(IST)
    assert start.date() == date(2026, 7, 2) and 9 <= start.hour < 17
    assert float(job.planned_cost_rs) < float(job.baseline_cost_rs)


def _demo_client(session, monkeypatch, demo=True, api_key=None):
    monkeypatch.setattr(main, "settings", replace(main.settings, demo_mode=demo, api_key=api_key))
    main.app.dependency_overrides[main.get_session] = lambda: session
    return TestClient(main.app)


def test_demo_endpoints_are_hidden_unless_demo_mode(session, monkeypatch):
    c = _demo_client(session, monkeypatch, demo=False)
    try:
        assert c.post("/demo/replay", json={}).status_code == 404
        assert c.post("/demo/live").status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_demo_replay_endpoint_starts_the_sim_clock_and_live_stops_it(session, monkeypatch):
    seed_tod(session)
    c = _demo_client(session, monkeypatch)
    try:
        r = c.post("/demo/replay", json={"start": "19:00", "scale": 120})
        assert r.status_code == 200, r.text
        assert clock.is_sim() and clock.scale() == 120
        body = r.json()
        assert body["mode"] == "replay" and body["prices_loaded"] == 192
        assert abs((clock.now() - datetime.fromisoformat(body["sim_start"])).total_seconds()) < 600  # ~5s real @120x
        assert c.post("/demo/live").json()["mode"] == "live" and not clock.is_sim()
    finally:
        main.app.dependency_overrides.clear()


@pytest.mark.parametrize("body", [{"scale": 0}, {"scale": 100000}, {"start": "nope"}])
def test_demo_replay_validates_input(session, monkeypatch, body):
    c = _demo_client(session, monkeypatch)
    try:
        assert c.post("/demo/replay", json=body).status_code == 422
        assert not clock.is_sim()
    finally:
        main.app.dependency_overrides.clear()


def test_demo_endpoints_honour_the_api_key(session, monkeypatch):
    c = _demo_client(session, monkeypatch, api_key="s3cret")
    try:
        assert c.post("/demo/replay", json={}).status_code == 401
        assert c.post("/demo/replay", json={}, headers={"X-API-Key": "s3cret"}).status_code == 200
    finally:
        main.app.dependency_overrides.clear()
