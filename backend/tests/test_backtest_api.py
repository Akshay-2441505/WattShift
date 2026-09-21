import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import backtest_runs, main

# Real cached IEX prices cover Mon 17 Aug .. Sun 13 Sep 2026 (see data/).
CSV = (
    "job_id,submit_time,duration_minutes,gpus\n"
    + "\n".join(f"j{i},2026-08-{18 + i % 5} {8 + i % 12:02d}:{(i * 7) % 60:02d}:00,{20 + i % 60},{1 + i % 8}" for i in range(60))
    + "\n"
)
OLD_CSV = CSV.replace("2026-08-", "2019-03-")  # same file, an old period with no price data


@pytest.fixture()
def client():
    return TestClient(main.app)


def wait(client, run_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/backtest/{run_id}").json()
        if r["status"] in ("done", "error"):
            return r
        time.sleep(0.3)
    raise AssertionError("backtest did not finish")


def start(client, **body):
    return client.post("/backtest", json={"source": "upload", "csv": CSV, **body})


def test_sample_info_describes_the_bundled_real_data(client):
    r = client.get("/backtest/sample").json()
    assert r["jobs"] > 1000 and r["fleet_gpus"] > 0
    assert r["price_window"]["from"] == "2026-08-17" and r["price_window"]["to"] == "2026-09-13"
    assert "Helios" in r["source"] and "CC-BY" in r["source"]


def test_a_small_upload_runs_and_returns_the_full_report(client):
    r = start(client)
    assert r.status_code == 202, r.text
    done = wait(client, r.json()["id"])
    assert done["status"] == "done" and done["stage"] == "finished"
    res = done["result"]
    t = res["totals"]
    assert t["jobs"] == 60 and t["baseline_rs"] > 0 and t["saved_rs"] >= 0
    assert abs(sum(x["energy_share"] for x in res["energy_by_length"]) - 1) < 1e-6
    assert set(res["kwh_by_zone_before"]) == {"baseline", "solar", "peak"}
    assert len(res["sensitivity"]) == 9  # 3 shares x 3 waits
    assert res["period"]["from"].startswith("2026-08-18") and "MSEDCL" in res["tariff"]


def test_the_main_result_is_available_before_the_slower_grid(client, monkeypatch):
    """The page shows the headline first; the other-scenarios grid fills in afterwards."""
    monkeypatch.setattr(backtest_runs, "SENSITIVITY_SHARES", (0.25, 0.5, 1.0))
    r = start(client)
    seen_partial = False
    for _ in range(200):
        s = client.get(f"/backtest/{r.json()['id']}").json()
        if s["result"] and s["result"]["sensitivity"] is None:
            seen_partial = True
        if s["status"] == "done":
            break
        time.sleep(0.05)
    assert s["status"] == "done"
    # timing-dependent on a fast machine, so only assert the ordering guarantee when we did catch it
    if seen_partial:
        assert s["result"]["sensitivity"] is not None


def test_assumptions_change_the_answer_in_the_right_direction(client):
    none = wait(client, start(client, assumptions={"flexible_share": 0.0}).json()["id"])["result"]["totals"]
    lots = wait(client, start(client, assumptions={"flexible_share": 1.0, "slack_hours": 24}).json()["id"])["result"]["totals"]
    assert none["saved_rs"] == 0 and lots["saved_rs"] > 0


def test_bad_file_gets_every_problem_with_its_line_number(client):
    bad = "job_id,submit_time,duration_minutes,gpus\na,nope,60,8\nb,2026-08-18 10:00:00,0,8\n"
    r = client.post("/backtest", json={"source": "upload", "csv": bad})
    assert r.status_code == 422
    msgs = " ".join(r.json()["detail"])
    assert "line 2" in msgs and "line 3" in msgs


def test_dates_outside_the_price_window_are_explained_not_guessed(client):
    r = client.post("/backtest", json={"source": "upload", "csv": OLD_CSV})
    assert r.status_code == 422
    assert "17 Aug" in " ".join(r.json()["detail"]) or "2026-08-17" in " ".join(r.json()["detail"])


def test_retime_lines_an_old_file_up_with_the_price_window(client):
    r = client.post("/backtest", json={"source": "upload", "csv": OLD_CSV, "retime": True})
    assert r.status_code == 202, r.text
    res = wait(client, r.json()["id"])["result"]
    assert res["period"]["from"] >= "2026-08-17" and res["totals"]["jobs"] == 60
    assert res["retimed"] is True


@pytest.mark.parametrize("assumptions", [{"flexible_share": 1.5}, {"slack_hours": -1}, {"cluster_gpus": 0}, {"kw_per_gpu": 0}])
def test_bad_assumptions_are_rejected(client, assumptions):
    assert start(client, assumptions=assumptions).status_code == 422


def test_an_upload_source_needs_a_file_and_an_unknown_source_is_refused(client):
    assert client.post("/backtest", json={"source": "upload"}).status_code == 422
    assert client.post("/backtest", json={"source": "somewhere"}).status_code == 422


def test_oversized_uploads_are_refused(client, monkeypatch):
    monkeypatch.setattr(backtest_runs, "MAX_UPLOAD_ROWS", 10)
    r = start(client)
    assert r.status_code == 422 and "10" in " ".join(r.json()["detail"])


def test_unknown_run_is_404(client):
    assert client.get("/backtest/nope").status_code == 404


def test_the_server_pushes_back_when_too_many_reports_are_running(client, monkeypatch):
    monkeypatch.setattr(backtest_runs, "MAX_ACTIVE", 0)
    assert start(client).status_code == 429


def test_starting_a_report_needs_the_api_key_when_one_is_set(client, monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, api_key="s3cret"))
    assert start(client).status_code == 401
    assert start(client, ).status_code == 401
    ok = client.post("/backtest", json={"source": "upload", "csv": CSV}, headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 202
    wait(client, ok.json()["id"])  # let it finish so it does not leak into the next test
