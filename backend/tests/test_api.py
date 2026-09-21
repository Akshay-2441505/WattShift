import itertools
import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import main
from app.seed import seed_tod
from tests.helpers import NOW, STEP, add_prices, evening_to_next_noon

DEADLINE = NOW + timedelta(hours=17)
BODY = {"duration_minutes": 15, "deadline": DEADLINE.isoformat(), "provider": "kaggle"}


@pytest.fixture()
def client(session):
    seed_tod(session)
    add_prices(session, NOW, evening_to_next_noon())
    ticks = itertools.count()  # each request happens a second later, like real traffic
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: NOW + timedelta(seconds=next(ticks))
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_submit_returns_scheduled_job_with_window_and_zone(client):
    r = client.post("/jobs", json=BODY)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["status"] == "scheduled"
    assert j["tod_zone"] == "solar"
    assert j["assigned_window_start"] > NOW.isoformat()
    assert j["planned_cost_rs"] < j["baseline_cost_rs"]


def test_list_and_detail(client):
    a = client.post("/jobs", json=BODY).json()
    b = client.post("/jobs", json={**BODY, "duration_minutes": 30}).json()
    listed = client.get("/jobs").json()
    assert [j["id"] for j in listed] == [b["id"], a["id"]]  # newest first
    assert client.get(f"/jobs/{a['id']}").json()["id"] == a["id"]


def test_unknown_job_is_404(client):
    assert client.get(f"/jobs/{uuid.uuid4()}").status_code == 404


@pytest.mark.parametrize(
    "patch",
    [
        {"duration_minutes": 0},
        {"duration_minutes": 500},
        {"provider": "runpod"},
        {"deadline": "2026-07-02T12:00:00"},  # naive
        {"deadline": (NOW + timedelta(minutes=5)).isoformat()},  # cannot finish in time
        {"power_kw": -1},
    ],
)
def test_bad_input_is_422_and_nothing_is_stored(client, patch):
    assert client.post("/jobs", json={**BODY, **patch}).status_code == 422
    assert client.get("/jobs").json() == []


def test_no_capacity_is_409_but_job_is_kept(session):
    seed_tod(session)  # no prices at all
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: NOW
    try:
        c = TestClient(main.app)
        r = c.post("/jobs", json=BODY)
        assert r.status_code == 409
        assert r.json()["job"]["status"] == "queued"
        assert [j["status"] for j in c.get("/jobs").json()] == ["queued"]
    finally:
        main.app.dependency_overrides.clear()


def test_api_key_guards_writes_only(client, monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, api_key="s3cret"))
    assert client.post("/jobs", json=BODY).status_code == 401
    assert client.post("/jobs", json=BODY, headers={"X-API-Key": "nope"}).status_code == 401
    assert client.post("/jobs", json=BODY, headers={"X-API-Key": "s3cret"}).status_code == 201
    assert client.get("/jobs").status_code == 200


def test_health(client):
    assert client.get("/health").json() == {"ok": True}
