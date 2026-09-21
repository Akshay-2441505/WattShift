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
