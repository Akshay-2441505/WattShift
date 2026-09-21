import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

import cloud
import stubcloud

IST = cloud.IST


def t(h, m):
    return datetime(2026, 9, 21, h, m, tzinfo=IST)


def test_hour_rules_cover_every_hour_once_and_mark_the_peak():
    rules = cloud.hour_rules({22, 23, 0})
    zone = {h: r["zone"] for r in rules for h in range(r["start_hour"], r["end_hour"])}
    assert sorted(zone) == list(range(24)) and sum(r["end_hour"] - r["start_hour"] for r in rules) == 24
    assert {h for h, z in zone.items() if z == "peak"} == {22, 23, 0}
    assert all(r["season"] is None for r in rules)
    assert {r["adj_pct"] for r in rules if r["zone"] == "peak"} == {25} and {r["adj_pct"] for r in rules if r["zone"] == "solar"} == {-15}


def test_the_rules_load_through_the_clouds_own_tariff_code():
    from app.catalogue import _rules_from_json
    from app.tariff import tod_multiplier

    rules = _rules_from_json(cloud.hour_rules({10}))
    assert tod_multiplier(t(10, 30), rules) == 1.25 and tod_multiplier(t(11, 0), rules) == 0.85


def test_choose_boundary_keeps_the_minimum_lead():
    assert cloud.choose_boundary(t(10, 5)) == t(11, 0)  # 55 minutes of lead
    assert cloud.choose_boundary(t(10, 47)) == t(11, 0)  # exactly 13
    assert cloud.choose_boundary(t(10, 48)) == t(12, 0)  # 12 minutes: too tight, take the next one
    assert cloud.choose_boundary(t(23, 30)) == t(23, 30).replace(hour=0, minute=0) + timedelta(days=1)  # wraps midnight


def test_peak_hours_run_from_now_to_the_boundary():
    assert cloud.peak_hours(t(10, 5), t(11, 0)) == {10}
    assert cloud.peak_hours(t(10, 40), t(12, 0)) == {10, 11}
    assert cloud.peak_hours(t(23, 10), t(23, 10).replace(hour=1, minute=0) + timedelta(days=1)) == {23, 0}


def test_the_stand_in_hands_out_one_start_time_until_the_agent_confirms_it():
    stub = stubcloud.StubCloud("k", delay_s=100)
    stub.targets = {"7"}
    body = {"jobs": [{"ref": "7", "state": "PENDING"}, {"ref": "8", "state": "PENDING"}], "applied": []}
    first = stub.handle(body)
    assert [d["ref"] for d in first["decisions"]] == ["7"]  # only the target job gets a decision
    assert stub.handle(body)["decisions"] == first["decisions"]  # the same time again, not a new one
    stub.handle({"jobs": [], "applied": [{"ref": "7", "start_at": "x", "ok": True}]})
    assert stub.handle(body)["decisions"] == []  # confirmed: stop sending it
    assert stub.handle(body)["next_poll_s"] == 10 and stub.handle(body)["release_all"] is False


def test_the_stand_in_start_time_is_in_the_future_and_in_the_agents_format():
    stub = stubcloud.StubCloud("k", delay_s=150)
    stub.targets = {"7"}
    out = stub.handle({"jobs": [{"ref": "7", "state": "PENDING"}], "applied": []})["decisions"][0]["start_at"]
    assert out.endswith("Z") and len(out) == 20  # 2026-09-21T14:13:20Z
    assert stub.decided["7"] > __import__("time").time() + 100


def test_the_stand_in_checks_the_site_key_over_http():
    stub = stubcloud.StubCloud("secret", port=0)
    stub.start()
    try:
        def post(key):
            req = urllib.request.Request(stub.base + "/agent/v1/sync", data=json.dumps({"jobs": [], "applied": []}).encode(),
                                         method="POST", headers={"Content-Type": "application/json", "X-Site-Key": key})
            return urllib.request.urlopen(req, timeout=5)

        assert json.loads(post("secret").read())["next_poll_s"] == 10
        with pytest.raises(urllib.error.HTTPError) as e:
            post("wrong")
        assert e.value.code == 401
        assert json.loads(urllib.request.urlopen(stub.base + "/health", timeout=5).read()) == {"ok": True}
    finally:
        stub.stop()
    with pytest.raises(OSError):
        urllib.request.urlopen(stub.base + "/health", timeout=2)
