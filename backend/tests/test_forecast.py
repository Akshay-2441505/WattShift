from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import clock, main
from app.config import CARBON_PROXY, settings
from app.seed import seed_tod
from tests.helpers import NOW, PEAK_MULT, STEP, add_prices, evening_to_next_noon


@pytest.fixture()
def client(session):
    seed_tod(session)
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: NOW
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()
    clock.reset()


def test_forecast_values_zones_and_carbon(client, session):
    add_prices(session, NOW, evening_to_next_noon())
    body = client.get("/forecast").json()
    assert body["mode"] == "live" and body["carbon_modeled"] is True and body["source"] == "iex_dam"
    b = body["blocks"]
    assert len(b) == 68  # only the blocks that have prices, within the next 24h
    first = b[0]
    assert datetime.fromisoformat(first["ts"]) == NOW  # the same instant (19:00 IST), whatever the tz spelling
    assert first["tod_zone"] == "peak" and first["tod_multiplier"] == pytest.approx(PEAK_MULT)
    assert first["iex_price"] == 10000 and first["effective_rate"] == pytest.approx(10000 * PEAK_MULT)
    assert first["billed_rs_kwh"] == pytest.approx(settings.base_rate_rs_kwh * PEAK_MULT)
    assert first["carbon_index"] == CARBON_PROXY[19]
    cheap = b[60]  # next day 10:00 IST
    assert cheap["tod_zone"] == "solar" and cheap["tod_multiplier"] == pytest.approx(0.85)
    assert cheap["carbon_index"] == CARBON_PROXY[10]
    assert cheap["effective_rate"] == pytest.approx(3000 * 0.85)


def test_forecast_excludes_past_blocks_and_respects_hours(client, session):
    add_prices(session, NOW - 4 * STEP, [1.0] * 4)  # the last hour: already past
    add_prices(session, NOW, [2.0] * 20)
    assert len(client.get("/forecast").json()["blocks"]) == 20
    assert len(client.get("/forecast?hours=1").json()["blocks"]) == 4


def test_forecast_with_no_prices_is_empty_not_an_error(client):
    r = client.get("/forecast")
    assert r.status_code == 200 and r.json()["blocks"] == []


def test_forecast_reads_replay_prices_only_in_sim_mode(client, session):
    add_prices(session, NOW, [5.0] * 8, source="iex_dam_replay")
    assert client.get("/forecast").json()["blocks"] == []
    clock.start_sim(NOW, scale=10)
    body = client.get("/forecast").json()
    assert body["mode"] == "replay" and body["sim_scale"] == 10 and body["source"] == "iex_dam_replay"
    assert len(body["blocks"]) == 8


def test_carbon_curve_is_a_24_hour_index():
    assert len(CARBON_PROXY) == 24 and all(0 <= v <= 100 for v in CARBON_PROXY)
    assert min(CARBON_PROXY) == min(CARBON_PROXY[9:16])  # cleanest during solar hours
    assert max(CARBON_PROXY) == max(CARBON_PROXY[17:22])  # dirtiest in the evening peak
