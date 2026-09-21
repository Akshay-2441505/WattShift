import itertools
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import main, sites
from app.catalogue import seed_catalogue
from app.seed import seed_tod
from tests.helpers import NOW


@pytest.fixture()
def env(session):
    seed_tod(session)
    seed_catalogue(session)
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=64)
    ticks = itertools.count()
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: NOW + timedelta(seconds=next(ticks))
    yield TestClient(main.app), site
    main.app.dependency_overrides.clear()


def test_sites_are_listed(env):
    client, site = env
    out = client.get("/sites").json()
    assert out == [{"id": str(site.id), "company": "Acme", "name": "Pune-1", "mode": "shadow", "release_all": False, "gpus": 64,
                    "last_seen_at": None, "agent_version": None}]


def test_the_view_has_everything_the_page_needs(env):
    client, site = env
    r = client.get(f"/sites/{site.id}/view")
    assert r.status_code == 200
    v = r.json()
    assert set(v) == {"site", "now", "summary", "jobs", "activity", "zones"}
    assert v["site"]["id"] == str(site.id) and v["summary"]["counts"] == {}


def test_an_unknown_site_is_404(env):
    client, _ = env
    assert client.get(f"/sites/{uuid.uuid4()}/view").status_code == 404
