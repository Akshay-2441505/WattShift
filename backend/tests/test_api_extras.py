from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.models import Job
from app.savings import record_savings
from app.seed import seed_tod
from app.tariff import IST
from tests.helpers import NOW, PEAK_MULT

SOLAR = datetime(2026, 7, 2, 12, tzinfo=IST)


@pytest.fixture()
def client(session):
    seed_tod(session)
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: NOW
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_tariff_endpoint_lists_rules_source_and_verified_flag(client):
    body = client.get("/tariff").json()
    assert body["base_rate_rs_kwh"] == settings.base_rate_rs_kwh
    assert body["verified"] is True
    assert "Case No. 75 of 2025" in body["source"] and body["valid_until"] == "2027-03-31"
    assert body["notes"] and any("+25%" in n for n in body["notes"])  # the one uncertain number is disclosed
    assert body["season_now"] == "apr_sep"  # NOW is 1 July
    zones = {(r["zone"], r["season"]): r for r in body["rules"]}
    assert zones[("peak", None)]["start_hour"] == 17 and zones[("peak", None)]["end_hour"] == 24
    assert zones[("solar", "oct_mar")]["adj_pct"] == -25


def test_tariff_verified_flag_follows_config(client, monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, tariff_verified=False))
    assert client.get("/tariff").json()["verified"] is False


def test_tariff_goes_unverified_after_the_period_the_order_covers(client):
    """MERC re-sets the numbers every 1 April (solar rebate -> 20/30%, energy charge -> Rs 8.23)."""
    main.app.dependency_overrides[main.get_now] = lambda: datetime(2027, 3, 31, 23, 0, tzinfo=IST)
    assert client.get("/tariff").json()["verified"] is True
    main.app.dependency_overrides[main.get_now] = lambda: datetime(2027, 4, 1, 0, 30, tzinfo=IST)
    assert client.get("/tariff").json()["verified"] is False


def test_done_job_exposes_savings_and_queued_job_does_not(client, session):
    done = Job(duration_minutes=60, deadline=SOLAR + timedelta(hours=5), provider="kaggle", status="done",
               power_kw=10, assigned_window_start=SOLAR, executed_at=SOLAR,
               baseline_cost_rs=10 * settings.base_rate_rs_kwh * PEAK_MULT)
    queued = Job(duration_minutes=15, deadline=SOLAR + timedelta(hours=5), provider="kaggle", status="queued",
                 baseline_cost_rs=5)
    session.add_all([done, queued])
    session.flush()
    record_savings(session, done)

    by_id = {j["id"]: j for j in client.get("/jobs").json()}
    d, q = by_id[str(done.id)], by_id[str(queued.id)]
    assert d["saved_rs"] == pytest.approx(10 * settings.base_rate_rs_kwh * (PEAK_MULT - 0.85), abs=0.01)
    assert d["actual_cost_rs"] == pytest.approx(10 * settings.base_rate_rs_kwh * 0.85, abs=0.01)
    assert q["saved_rs"] is None and q["actual_cost_rs"] is None
    assert client.get(f"/jobs/{done.id}").json()["saved_rs"] == d["saved_rs"]
