"""Attacker's-eye tests: who may write, what a hostile request can make the server do, what the responses give away.
These were written to FIND holes (see docs/security/2026-09-21-audit.md); they stay as regression tests."""
import hashlib
import json
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import backtest_runs, main, sites
from app.main import check_production
from app.models import Job
from app.seed import seed_tod
from tests.helpers import NOW, add_prices, evening_to_next_noon

KEY = "s3cret-key-for-tests"
BACKEND = Path(__file__).resolve().parents[1]
DEADLINE = (NOW + timedelta(hours=17)).isoformat()
GOOD_JOB = {"duration_minutes": 15, "deadline": DEADLINE, "provider": "kaggle"}
SOME_SITE = str(uuid.uuid4())


@pytest.fixture()
def client(session, monkeypatch):
    seed_tod(session)
    add_prices(session, NOW, evening_to_next_noon())
    monkeypatch.setattr(main, "settings", replace(main.settings, api_key=KEY, demo_mode=True))
    monkeypatch.setattr(backtest_runs, "start", lambda *a, **k: "fake-run")  # never start real CPU-heavy reports
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: NOW
    yield TestClient(main.app, raise_server_exceptions=False)  # a crash must show up as a 500, not as a test error
    main.app.dependency_overrides.clear()


WRITES = [
    ("post", "/jobs", GOOD_JOB),
    ("post", "/backtest", {"source": "sample"}),
    ("post", f"/sites/{SOME_SITE}/mode", {"mode": "autonomous"}),
    ("post", f"/sites/{SOME_SITE}/release-all", {"on": True}),
    ("post", "/demo/replay", {"start": "19:00", "scale": 60}),
    ("post", "/demo/live", {}),
]


@pytest.mark.parametrize("method, path, body", WRITES)
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": ""}, {"X-API-Key": "wrong"}, {"X-API-Key": KEY[:-1] + "X"}, {"X-API-Key": KEY + " "}, {"x-api-key": KEY.upper()}])
def test_every_write_endpoint_refuses_a_missing_or_wrong_key(client, method, path, body, headers):
    assert getattr(client, method)(path, json=body, headers=headers).status_code == 401


@pytest.mark.parametrize("method, path, body", WRITES[:3])
def test_the_right_key_is_accepted(client, method, path, body):
    assert getattr(client, method)(path, json=body, headers={"X-API-Key": KEY}).status_code != 401


def test_the_agent_endpoint_refuses_anything_but_a_live_site_key(client, session):
    site, key = sites.create_site(session, "Acme", "Pune", gpus=8)
    body = {"agent_version": "0", "mode": "shadow", "sent_at": NOW.isoformat(), "jobs": []}
    assert client.post("/agent/v1/sync", json=body).status_code == 401
    for bad in ("", "wsk_nope", key[:-1], key + "x", sites.hash_key(key), hashlib.sha256(key.encode()).hexdigest().upper(), KEY):
        assert client.post("/agent/v1/sync", json=body, headers={"X-Site-Key": bad}).status_code == 401, bad
    assert client.post("/agent/v1/sync", json=body, headers={"X-Site-Key": key}).status_code == 200
    sites.revoke_keys(session, site, NOW)
    assert client.post("/agent/v1/sync", json=body, headers={"X-Site-Key": key}).status_code == 401  # revoked means revoked


def test_the_global_api_key_is_not_a_site_key_and_a_site_key_is_not_the_api_key(client, session):
    site, key = sites.create_site(session, "Acme", "Pune", gpus=8)
    assert client.post(f"/sites/{site.id}/mode", json={"mode": "autonomous"}, headers={"X-API-Key": key}).status_code == 401


def test_demo_endpoints_do_not_exist_when_demo_mode_is_off(client, monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, api_key=KEY, demo_mode=False))
    for path in ("/demo/replay", "/demo/live"):
        assert client.post(path, json={}, headers={"X-API-Key": KEY}).status_code == 404


# --- hostile input must never crash the server -------------------------------------------------------------------

BAD_JOBS = [
    {}, [], "x", None, 5, {"duration_minutes": 0}, {"duration_minutes": -5}, {"duration_minutes": 10**30}, {"duration_minutes": 1.5}, {"duration_minutes": True},
    {**GOOD_JOB, "duration_minutes": "60"}, {**GOOD_JOB, "duration_minutes": [15]}, {**GOOD_JOB, "deadline": "not a date"}, {**GOOD_JOB, "deadline": "0000-01-01T00:00:00Z"},
    {**GOOD_JOB, "deadline": "9999-12-31T23:59:59Z"}, {**GOOD_JOB, "deadline": "2026-07-02T10:00:00"}, {**GOOD_JOB, "deadline": 0}, {**GOOD_JOB, "deadline": {"a": 1}},
    {**GOOD_JOB, "provider": "'; drop table jobs;--"}, {**GOOD_JOB, "provider": "KAGGLE"}, {**GOOD_JOB, "provider": None},
    {**GOOD_JOB, "power_kw": "NaN"}, {**GOOD_JOB, "power_kw": "Infinity"}, {**GOOD_JOB, "power_kw": -1}, {**GOOD_JOB, "power_kw": 1e308}, {**GOOD_JOB, "power_kw": "1e400"},
    {**GOOD_JOB, "extra": "x" * 100_000}, {**GOOD_JOB, "__proto__": {"admin": True}},
]


@pytest.mark.parametrize("body", BAD_JOBS, ids=[f"job{i}" for i in range(len(BAD_JOBS))])
def test_a_hostile_job_is_rejected_or_accepted_but_never_a_server_error(client, body):
    r = client.post("/jobs", json=body, headers={"X-API-Key": KEY})
    assert r.status_code < 500, (body, r.text[:200])


@pytest.mark.parametrize("raw", [b"{", b"\xff\xfe\x00", b"", b"null", b"[" * 5000, b'{"duration_minutes": ' + b"9" * 5000 + b"}"])
def test_a_malformed_body_is_a_client_error(client, raw):
    r = client.post("/jobs", content=raw, headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    assert 400 <= r.status_code < 500


ODD_PATHS = ["/jobs/not-a-uuid", "/jobs/" + "a" * 5000, f"/sites/{SOME_SITE}/view", "/sites/x/view", "/backtest/../../etc/passwd", "/backtest/%00", "/backtest/" + "z" * 5000,
             "/forecast?hours=0", "/forecast?hours=-1", "/forecast?hours=999999999999", "/forecast?hours=abc", "/forecast?hours=1&hours=2", "/measured?x=" + "y" * 10000, "/nope", "//jobs"]


@pytest.mark.parametrize("path", ODD_PATHS, ids=[f"path{i}" for i in range(len(ODD_PATHS))])
def test_odd_urls_are_client_errors_or_ok(client, path):
    assert client.get(path).status_code < 500


def csv(rows: list[str]) -> str:
    return "job_id,submit_time,duration_minutes,gpus\n" + "\n".join(rows)


BAD_CSVS = [
    "", "\x00" * 1000, "\ufeff", csv([]), csv(["j,2026-08-20 10:00:00,60,8"] * 3), csv(["=cmd|' /C calc'!A0,2026-08-20 10:00:00,60,8"]), csv(["j,2026-08-20 10:00:00,1e999,8"]),
    csv(["j,2026-08-20 10:00:00,-5,8"]), csv(["j,2026-08-20 10:00:00,60,NaN"]), csv(["j,not-a-date,60,8"]), csv(["j,2026-08-20 10:00:00,60,8," + "x" * 100_000]),
    csv([f"j{i},2026-08-20 10:00:00,60,8" for i in range(25_000)]), "a," * 50_000, csv(["j,9999-12-31 10:00:00,60,8"]), csv(["j,0001-01-01 10:00:00,60,8"]),
    csv(["\"unterminated,2026-08-20 10:00:00,60,8"]), "job_id,submit_time,duration_minutes,gpus\r\n" + "j,2026-08-20 10:00:00,60,8\r\n" * 5,
]


@pytest.mark.parametrize("text", BAD_CSVS, ids=[f"csv{i}" for i in range(len(BAD_CSVS))])  # short ids: a 100 kB id overflows a Windows environment variable
@pytest.mark.parametrize("retime", [False, True])
def test_a_hostile_upload_is_a_client_error_never_a_server_error(client, text, retime):
    r = client.post("/backtest", json={"source": "upload", "csv": text, "retime": retime}, headers={"X-API-Key": KEY})
    assert r.status_code < 500, (text[:60], r.text[:200])


@pytest.mark.parametrize("assumptions", [{"flexible_share": 9}, {"slack_hours": -1}, {"kw_per_gpu": 0}, {"cluster_gpus": 10**12}, {"cluster_gpus": "many"}, {"shift_capacity_share": "NaN"}, {"flexible_share": None}])
def test_absurd_assumptions_are_refused(client, assumptions):
    r = client.post("/backtest", json={"source": "sample", "assumptions": assumptions}, headers={"X-API-Key": KEY})
    assert 400 <= r.status_code < 500


def sync_body(**kw):
    return {"agent_version": "0", "mode": "shadow", "sent_at": NOW.isoformat(), "jobs": [], **kw}


BAD_SYNCS = [
    {}, [], sync_body(mode="root"), sync_body(sent_at="x"), sync_body(agent_version="v" * 500), sync_body(jobs=[{"ref": "", "state": "PENDING"}]),
    sync_body(jobs=[{"ref": "1" * 65, "state": "PENDING"}]), sync_body(jobs=[{"ref": "1", "state": "DROP"}]), sync_body(jobs=[{"ref": "1\x00", "state": "PENDING"}]),
    sync_body(jobs=[{"ref": "1", "state": "PENDING", "gpus": -1}]), sync_body(jobs=[{"ref": "1", "state": "PENDING", "gpus": 10**12}]),
    sync_body(jobs=[{"ref": str(i), "state": "PENDING"} for i in range(2001)]), sync_body(released=["x"] * 2001), sync_body(applied=[{"ref": "1", "start_at": "x", "ok": True}]),
    sync_body(jobs=[{"ref": "1", "state": "PENDING", "submit_time": "2026-07-01T10:00:00"}]),  # naive time
    sync_body(jobs=[{"ref": "1", "state": "PENDING", "submit_time": "9999-12-31T23:59:59Z", "time_limit_min": 60, "gpus": 1, "max_wait_min": 600}]),
    sync_body(jobs=[{"ref": "1", "state": "PENDING", "submit_time": NOW.isoformat(), "start_time": "0001-01-01T00:00:00Z"}]),
    sync_body(jobs=[{"ref": "'; delete from jobs; --", "state": "PENDING"}]), sync_body(jobs=[{"ref": "é" * 64, "state": "COMPLETED", "end_time": NOW.isoformat()}]),
]


@pytest.mark.parametrize("body", BAD_SYNCS, ids=[f"sync{i}" for i in range(len(BAD_SYNCS))])
def test_a_hostile_agent_report_is_never_a_server_error(client, session, body):
    _, key = sites.create_site(session, "Acme", "Pune", gpus=8)
    r = client.post("/agent/v1/sync", json=body, headers={"X-Site-Key": key})
    assert r.status_code < 500, (str(body)[:120], r.text[:200])


# --- what the API gives away, and what it lets a stranger do ------------------------------------------------------


def test_a_job_row_does_not_reveal_the_owners_kaggle_account(client, session):
    session.add(Job(duration_minutes=15, deadline=NOW + timedelta(hours=3), provider="kaggle", status="done", external_ref="someone-private/wattshift-abc123"))
    session.flush()
    body = client.get("/jobs").text
    assert "someone-private" not in body and "external_ref" not in body


def test_a_failure_reason_does_not_leak_paths_or_secrets(client, session):
    reason = r"start failed: RuntimeError: kaggle kernels failed: C:\Users\aakur\.kaggle\access_token unreadable, token wsk_" + "A" * 43
    session.add(Job(duration_minutes=15, deadline=NOW + timedelta(hours=3), provider="kaggle", status="failed", failure_reason=reason))
    session.flush()
    from app import dispatch

    j = session.query(Job).filter(Job.status == "failed").one()
    dispatch._fail(session, j, reason)
    text = client.get("/jobs").text
    assert "aakur" not in text and "wsk_" + "A" * 43 not in text


def test_responses_carry_basic_hardening_headers(client):
    h = client.get("/health").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer" and "no-store" in h["cache-control"]


def test_an_oversized_body_is_refused_before_it_is_read(client):
    r = client.post("/jobs", content=b"x" * 10, headers={"X-API-Key": KEY, "Content-Length": str(50_000_000), "Content-Type": "application/json"})
    assert r.status_code == 413


def test_a_foreign_origin_gets_no_cors_permission_by_default(client):
    r = client.options("/jobs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
    assert "access-control-allow-origin" not in client.get("/jobs", headers={"Origin": "https://evil.example"}).headers


def test_the_number_of_waiting_jobs_is_capped_so_a_leaked_key_cannot_flood_the_queue(client, monkeypatch):
    monkeypatch.setattr(main.service, "settings", replace(main.service.settings, max_active_jobs=3))
    codes = [client.post("/jobs", json=GOOD_JOB, headers={"X-API-Key": KEY}).status_code for _ in range(5)]
    assert codes[:3] == [201] * 3 and codes[3:] == [429, 429]


# --- deploy guards -----------------------------------------------------------------------------------------------


def test_production_refuses_demo_mode_and_a_missing_key():
    with pytest.raises(RuntimeError):
        check_production(SimpleNamespace(production=True, api_key="k", demo_mode=True))  # the demo endpoints can wipe every job
    with pytest.raises(RuntimeError):
        check_production(SimpleNamespace(production=True, api_key="", demo_mode=False))
    check_production(SimpleNamespace(production=True, api_key="k", demo_mode=False))
    check_production(SimpleNamespace(production=False, api_key="", demo_mode=True))  # local development is unaffected


def run_app_module(code: str, **env) -> str:
    import os

    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=60, env={**os.environ, **env})
    assert out.returncode == 0, out.stderr[-400:]
    return out.stdout.strip()


def test_the_interactive_docs_are_switched_off_in_production():
    code = "from app import main; print(main.app.docs_url, main.app.redoc_url, main.app.openapi_url)"
    assert run_app_module(code, PRODUCTION="1", API_KEY="k") == "None None None"
    assert run_app_module(code, PRODUCTION="") == "/docs /redoc /openapi.json"


def test_a_settings_object_never_prints_its_secrets():
    """Logging or repr()-ing the settings (a common debugging line) must not spill the API key or the database password."""
    s = replace(main.settings, api_key="hunter2-long-api-key", database_url="postgresql://u:dbpass@h/db")
    assert "hunter2" not in repr(s) and "dbpass" not in repr(s)


def test_the_audit_trail_cannot_be_rewritten_even_by_the_application_itself(session):
    from sqlalchemy import delete, update
    from sqlalchemy.exc import DBAPIError

    from app import audit
    from app.models import AuditLog

    site, _ = sites.create_site(session, "Acme", "Pune", gpus=8)
    audit.log(session, site.id, NOW, "agent", "job_seen", ref="1")
    session.flush()
    with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
        session.execute(update(AuditLog).values(event="nothing happened"))
    with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
        session.execute(delete(AuditLog))
    assert session.query(AuditLog).count() == 1
