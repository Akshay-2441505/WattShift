import uuid
from datetime import datetime, timedelta, timezone

import pytest

import cloud
import timelapse


def at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


@pytest.fixture()
def restore(monkeypatch):
    """apply() changes module attributes; register the originals so every test undoes it."""
    from app import agent_sync, allocator, planner, tariff

    for mod, name in [(allocator, "BLOCK_MIN"), (allocator, "BLOCK"), (allocator, "jitter_minutes"), (planner, "BLOCK_MIN"),
                      (tariff, "tod_zone"), (agent_sync, "POLL_SECONDS")]:
        monkeypatch.setattr(mod, name, getattr(mod, name))


def test_a_period_is_one_pseudo_hour_and_it_wraps_at_24():
    assert timelapse.pseudo_hour(at(120 * 1000)) == 1000 % 24
    assert timelapse.pseudo_hour(at(120 * 1000 + 119)) == 1000 % 24
    assert timelapse.pseudo_hour(at(120 * 1001)) == 1001 % 24
    assert {timelapse.pseudo_hour(at(120 * i)) for i in range(48)} == set(range(24))


def test_the_boundary_is_a_period_edge_with_enough_lead():
    base = 120 * 1000
    assert timelapse.choose_boundary(at(base + 10)) == at(base + 240)  # the next edge is only 110 s away: too tight, the one after is exactly 230 s
    assert timelapse.choose_boundary(at(base + 100)) == at(base + 360)  # edges 20 s and 140 s away are both under the 230 s lead: take the third
    assert timelapse.choose_boundary(at(base)) == at(base + 240)  # exactly on an edge: the next is 120 s away, too tight


def test_the_peak_covers_the_pseudo_hours_from_now_to_the_boundary():
    now = at(120 * 1000 + 10)
    b = timelapse.choose_boundary(now)
    assert timelapse.peak_pseudo_hours(now, b) == {1000 % 24, 1001 % 24}


def test_the_patched_zone_lookup_uses_pseudo_hours(restore):
    from app.catalogue import _rules_from_json

    rules = _rules_from_json(cloud.hour_rules({1000 % 24, 1001 % 24}))
    assert timelapse.tod_zone(at(120 * 1000 + 5), rules).zone == "peak"
    assert timelapse.tod_zone(at(120 * 1002 + 5), rules).zone == "solar"
    with pytest.raises(ValueError):
        timelapse.tod_zone(datetime(2026, 9, 21, 10, 0), rules)  # naive time


def test_apply_switches_the_allocator_to_one_minute_blocks_without_a_spread(restore):
    from app import agent_sync, allocator, planner, tariff

    timelapse.apply()
    t = datetime(2026, 9, 21, 10, 34, 56, tzinfo=timezone.utc)
    assert allocator.floor_block(t) == t.replace(second=0)  # a minute, not a quarter hour
    assert allocator.overlaps(t.replace(second=0), 3) == [(t.replace(second=0) + timedelta(minutes=i), 1) for i in range(3)]
    assert all(allocator.jitter_minutes(f"job-{i}") == 0 for i in range(30))  # 1-minute blocks make the per-job spread (hash % BLOCK_MIN) zero; with 15-minute blocks these would mostly not be 0
    assert planner.BLOCK_MIN == 1 and agent_sync.POLL_SECONDS == 10
    assert tariff.tod_zone is timelapse.tod_zone


def test_the_real_planner_defers_a_job_to_the_time_lapse_boundary(restore):
    """The real sync + planner + tariff code, on the time-lapse tariff, against the test database."""
    from sqlalchemy.orm import Session

    from app import agent_sync, db
    from app.models import Site

    timelapse.apply()
    now = at(120 * 1000 + 10)
    boundary = timelapse.choose_boundary(now)
    cloud.seed(now, timelapse.peak_pseudo_hours(now, boundary), step=timedelta(minutes=1), hours=3)
    job = {"ref": "1", "state": "PENDING", "submit_time": now.isoformat(), "gpus": 2, "time_limit_min": 1, "max_wait_min": 180}
    body = agent_sync.SyncIn.model_validate({"agent_version": "0", "mode": "autonomous", "sent_at": now.isoformat(), "jobs": [job]})
    with Session(db.make_engine(cloud.db_url())) as s, s.begin():
        site = s.get(Site, uuid.UUID(cloud.site_id()))
        site.mode = "autonomous"
        s.flush()
        out = agent_sync.process_sync(s, site, body, now)
    assert [d.start_at for d in out.decisions] == [boundary]  # exactly the moment the time-lapse tariff turns cheap
    assert out.next_poll_s == 10
