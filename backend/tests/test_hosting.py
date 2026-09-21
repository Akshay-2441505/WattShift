"""Hosting guards: a hosted service refuses to start without an API key, and only the configured dashboard origin may call it."""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.main import check_production

CORS_PROBE = """
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
ok = c.options("/jobs", headers={"Origin": "https://dash.example", "Access-Control-Request-Method": "POST",
                                 "Access-Control-Request-Headers": "x-api-key"})
bad = c.options("/jobs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
print(ok.headers.get("access-control-allow-origin"), bad.headers.get("access-control-allow-origin"))
"""


def test_production_without_an_api_key_refuses_to_start():
    with pytest.raises(RuntimeError, match="API_KEY"):
        check_production(SimpleNamespace(production=True, api_key=None))
    with pytest.raises(RuntimeError):
        check_production(SimpleNamespace(production=True, api_key=""))


def test_production_with_a_key_and_local_dev_without_one_both_start():
    check_production(SimpleNamespace(production=True, api_key="k"))
    check_production(SimpleNamespace(production=False, api_key=None))


def test_only_the_configured_origin_is_allowed_by_cors():
    """Settings are read when the app module is imported, so this runs in a fresh interpreter with the variable set."""
    out = subprocess.run([sys.executable, "-c", CORS_PROBE], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "CORS_ORIGINS": "https://dash.example"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["https://dash.example", "None"]


def test_the_root_address_points_at_the_api_docs():
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"


def test_with_no_compute_provider_the_dispatch_loop_runs_rarely_so_a_free_database_can_sleep(engine):
    """A hosted API cannot run Kaggle jobs, so a 5-second loop would only keep the database awake and use up its free hours."""
    from app import db, runner
    from app.config import settings
    from app.providers.fake import FakeProvider
    from tests.helpers import NOW

    factory = db.make_session_factory(engine)
    for providers, expected in (({}, settings.idle_dispatch_seconds), ({"kaggle": FakeProvider()}, 5)):
        sched = runner.start(factory, providers, dispatch_seconds=5, ingest=False, now=lambda: NOW)
        try:
            assert sched.get_job("dispatch").trigger.interval.total_seconds() == expected
        finally:
            sched.shutdown(wait=False)


def test_health_says_whether_this_server_can_run_jobs(monkeypatch):
    """The hosted API has no Kaggle token, so the Try it page must be able to say so instead of letting a job quietly fail later."""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app import main

    def can_run(**kw):
        monkeypatch.setattr(main, "settings", replace(main.settings, **kw))
        body = TestClient(main.app).get("/health").json()
        assert body["ok"] is True
        return body["can_run_jobs"]

    assert can_run(run_scheduler=True, kaggle_username="someone") is True
    assert can_run(run_scheduler=True, kaggle_username=None) is False  # no Kaggle account configured (a hosted API)
    assert can_run(run_scheduler=False, kaggle_username="someone") is False  # nothing is dispatching jobs
