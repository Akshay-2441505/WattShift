import itertools
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main, sites
from app.catalogue import seed_catalogue
from app.seed import seed_tod
from tests.helpers import NOW, add_prices, evening_to_next_noon

CHEAP_START = NOW + timedelta(hours=15)
CHEAP_END = NOW + timedelta(hours=16)


def payload(*jobs, mode="autonomous", **kw):
    return {"agent_version": "0.1.0", "mode": mode, "sent_at": NOW.isoformat(), "jobs": list(jobs), **kw}


def fact(ref, **kw):
    return {"ref": ref, "state": "PENDING", "submit_time": NOW.isoformat(), "gpus": 4, "time_limit_min": 30,
            "max_wait_min": 17 * 60, **kw}


@pytest.fixture()
def env(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon())
    site, key = sites.create_site(session, "Acme", "Pune-1", gpus=8, mode="autonomous")
    ticks = itertools.count()
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: NOW + timedelta(seconds=next(ticks))
    yield TestClient(main.app), site, {"X-Site-Key": key}
    main.app.dependency_overrides.clear()


def test_a_key_is_required(env):
    client, _, _ = env
    assert client.post("/agent/v1/sync", json=payload()).status_code == 401
    assert client.post("/agent/v1/sync", json=payload(), headers={"X-Site-Key": "wsk_wrong"}).status_code == 401


def test_a_revoked_key_is_refused(env, session):
    client, site, headers = env
    sites.revoke_keys(session, site, NOW)
    assert client.post("/agent/v1/sync", json=payload(), headers=headers).status_code == 401


def test_sync_returns_decisions_inside_the_cheap_window(env):
    client, _, headers = env
    # 4 + 1 + 1 GPUs x 30 min = 180 of the window's 240 GPU-minutes: it fits whatever each job's random start offset is
    # (with 4 + 2 + 2 it filled the window exactly and an unlucky offset pushed one job out: a flaky test, not a bug).
    r = client.post("/agent/v1/sync", json=payload(fact("1"), fact("2", gpus=1), fact("3", gpus=1)), headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert sorted(d["ref"] for d in out["decisions"]) == ["1", "2", "3"]
    assert out["release_all"] is False and out["next_poll_s"] == 30
    for d in out["decisions"]:
        start = datetime.fromisoformat(d["start_at"])
        assert CHEAP_START <= start <= CHEAP_END


def test_a_retried_request_gets_the_same_answer(env):
    client, _, headers = env
    body = payload(fact("1"))
    a = client.post("/agent/v1/sync", json=body, headers=headers).json()
    b = client.post("/agent/v1/sync", json=body, headers=headers).json()
    assert a["decisions"] == b["decisions"]


@pytest.mark.parametrize(
    "patch",
    [
        {"mode": "yolo"},
        {"sent_at": "2026-07-01T19:00:00"},  # naive time
        {"jobs": [{"ref": "1", "state": "WEIRD"}]},
        {"jobs": [{"ref": "", "state": "PENDING"}]},
        {"jobs": [{"ref": "1", "state": "PENDING", "gpus": -1}]},
    ],
)
def test_bad_input_is_422(env, patch):
    client, _, headers = env
    assert client.post("/agent/v1/sync", json={**payload(), **patch}, headers=headers).status_code == 422


def test_mode_switch_changes_what_the_agent_receives(env):
    client, site, headers = env
    assert client.post(f"/sites/{site.id}/mode", json={"mode": "shadow"}).json()["mode"] == "shadow"
    assert client.post("/agent/v1/sync", json=payload(fact("1")), headers=headers).json()["decisions"] == []
    client.post(f"/sites/{site.id}/mode", json={"mode": "autonomous"})
    assert len(client.post("/agent/v1/sync", json=payload(fact("1")), headers=headers).json()["decisions"]) == 1


def test_release_all_switch_reaches_the_agent(env):
    client, site, headers = env
    r = client.post(f"/sites/{site.id}/release-all", json={"on": True})
    assert r.json() == {"site_id": str(site.id), "mode": "autonomous", "release_all": True}
    out = client.post("/agent/v1/sync", json=payload(fact("1")), headers=headers).json()
    assert out["release_all"] is True and out["decisions"] == []


def test_unknown_site_is_404_and_bad_mode_is_422(env):
    client, site, _ = env
    assert client.post(f"/sites/{uuid.uuid4()}/mode", json={"mode": "shadow"}).status_code == 404
    assert client.post(f"/sites/{site.id}/mode", json={"mode": "yolo"}).status_code == 422
