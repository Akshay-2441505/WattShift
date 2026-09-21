import uuid
from datetime import datetime, timedelta

import pytest

from app import audit, site_views, sites
from app.catalogue import get_tariff, seed_catalogue
from app.models import AuditLog, ManagedJob
from app.seed import seed_tod
from app.tariff import IST
from tests.helpers import NOW

T = NOW + timedelta(hours=17)  # Thu 12:00 IST, solar


def j(**kw):
    base = dict(state="PENDING", plan_status="pending", note=None, planned_start=None, applied_start=None)
    return ManagedJob(**{**base, **kw})


@pytest.mark.parametrize(
    "job, want",
    [
        (j(), "seen"),
        (j(plan_status="planned", planned_start=T), "would_hold"),  # planned but the agent has not confirmed it (or shadow mode)
        (j(plan_status="planned", planned_start=T, applied_start=T), "held"),
        (j(plan_status="planned"), "runs_normally"),  # not worth moving: the bill would not be lower
        (j(plan_status="unplaceable"), "no_window"),
        (j(plan_status="skipped", note="array"), "skipped"),
        (j(plan_status="abandoned", note="user_changed"), "owner_changed"),
        (j(plan_status="abandoned", note="apply_failed: nope"), "left_alone"),
        (j(plan_status="released"), "released"),
        (j(state="RUNNING", plan_status="planned"), "running"),
        (j(state="COMPLETED"), "done"),
        (j(state="FAILED"), "ended"),
        (j(state="CANCELLED"), "ended"),
    ],
)
def test_display_state(job, want):
    assert site_views.display_state(job) == want


def test_activity_lines_are_plain_language():
    row = lambda event, ref=None, **d: AuditLog(event=event, ref=ref, detail=d or None)  # noqa: E731
    assert site_views.describe(row("job_seen", "48211")) == "Saw job 48211"
    assert site_views.describe(row("skipped", "50_[1-3]", reason="array")) == "Skipping job 50_[1-3] (array)"
    assert site_views.describe(row("mode_changed", mode="autonomous")) == "Mode changed to autonomous"
    assert "Kill switch on" in site_views.describe(row("release_all_on"))
    assert "site is in shadow" in site_views.describe(row("mode_mismatch", site_mode="shadow", agent_mode="autonomous"))
    assert "agent is in shadow" in site_views.describe(row("mode_mismatch", site_mode="autonomous", agent_mode="shadow"))
    assert site_views.describe(row("something_new")) == "something new"  # an unknown event is still readable


def test_zone_segments_merge_and_split_at_the_tariff_boundary(session):
    seed_tod(session)
    seed_catalogue(session)
    tariff = get_tariff(session, "MSEDCL", "HT-I(A)", datetime(2026, 7, 1).date())
    start = datetime(2026, 7, 1, 16, 55, tzinfo=IST)
    segs = site_views.zone_segments(tariff, start, start + timedelta(minutes=10))  # 1-minute steps
    assert [(s["zone"], s["start"], s["end"]) for s in segs] == [
        ("solar", start, start + timedelta(minutes=5)),
        ("peak", start + timedelta(minutes=5), start + timedelta(minutes=10)),
    ]
    day = site_views.zone_segments(tariff, datetime(2026, 7, 1, 0, 0, tzinfo=IST), datetime(2026, 7, 2, 0, 0, tzinfo=IST))  # 15-minute steps
    assert [s["zone"] for s in day] == ["baseline", "solar", "peak"]


def make(session, site, ref, **kw):
    base = dict(id=uuid.uuid4(), site_id=site.id, ref=ref, state="PENDING", plan_status="pending", first_seen=NOW, submit_time=NOW, gpus=4,
                time_limit_min=60, max_wait_min=1440)
    row = ManagedJob(**{**base, **kw})
    session.add(row)
    session.flush()
    return row


def test_the_view_separates_measured_savings_from_the_plan(session):
    seed_tod(session)
    seed_catalogue(session)
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8, mode="autonomous")
    make(session, site, "1", state="COMPLETED", plan_status="planned", applied_start=T, planned_start=T, actual_start=T,
         actual_end=T + timedelta(hours=1), baseline_start=NOW, baseline_cost=105.5, planned_cost=71.74, actual_cost=71.74, saved=33.76)
    make(session, site, "2", plan_status="planned", planned_start=T, applied_start=T, baseline_start=NOW, baseline_cost=50.0, planned_cost=30.0)
    make(session, site, "3", plan_status="skipped", note="array")
    audit.log(session, site.id, NOW, "agent", "job_seen", ref="2")
    audit.log(session, site.id, NOW + timedelta(seconds=5), "planner", "decision", ref="2")
    v = site_views.build_view(session, site, NOW)
    assert v["site"]["name"] == "Pune-1" and v["site"]["mode"] == "autonomous" and v["site"]["company"] == "Acme"
    s = v["summary"]
    assert (s["saved"], s["baseline"], s["jobs_measured"]) == (33.76, 105.5, 1) and s["pct_saved"] == 32.0
    assert s["potential"] == 20.0  # only job 2: pending with a planned deferral; job 1 is already measured
    assert s["counts"] == {"done": 1, "held": 1, "skipped": 1}
    assert {r["ref"]: r["display"] for r in v["jobs"]} == {"1": "done", "2": "held", "3": "skipped"}
    assert [a["event"] for a in v["activity"]] == ["decision", "job_seen"]  # newest first
    assert v["zones"] and {"start", "end", "zone"} <= set(v["zones"][0])


def test_the_view_still_works_when_the_tariff_has_expired(session):
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)  # no catalogue entry at all
    v = site_views.build_view(session, site, NOW)
    assert v["zones"] == [] and v["jobs"] == [] and v["summary"]["saved"] == 0 and v["summary"]["pct_saved"] is None


def test_the_timeline_window_reaches_back_to_jobs_that_have_already_run(session):
    """Finished jobs used to fall off the left edge of the timeline: the window started five minutes before now."""
    seed_tod(session)
    seed_catalogue(session)
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    make(session, site, "1", state="COMPLETED", plan_status="planned", applied_start=NOW - timedelta(hours=1), planned_start=NOW - timedelta(hours=1),
         actual_start=NOW - timedelta(hours=1), actual_end=NOW - timedelta(minutes=50), baseline_start=NOW - timedelta(minutes=95))
    v = site_views.build_view(session, site, NOW)
    first, last = v["zones"][0]["start"], v["zones"][-1]["end"]
    assert first <= NOW - timedelta(minutes=95) and last >= NOW + timedelta(minutes=15)
    # and a site with only current jobs keeps the short window
    site2, _ = sites.create_site(session, "Acme", "Pune-2", gpus=8)
    v2 = site_views.build_view(session, site2, NOW)
    assert v2["zones"][0]["start"] >= NOW - timedelta(minutes=6)


def test_the_timeline_does_not_stretch_over_a_long_idle_gap(session):
    """A demo left idle for hours squashed its jobs against the left edge, because the window always reached to now."""
    seed_tod(session)
    seed_catalogue(session)
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    end = NOW - timedelta(hours=5)
    make(session, site, "1", state="COMPLETED", plan_status="planned", applied_start=end - timedelta(minutes=10), planned_start=end - timedelta(minutes=10),
         actual_start=end - timedelta(minutes=10), actual_end=end, baseline_start=end - timedelta(minutes=15))
    v = site_views.build_view(session, site, NOW)
    first, last = v["zones"][0]["start"], v["zones"][-1]["end"]
    assert first <= end - timedelta(minutes=17) and end + timedelta(minutes=10) <= last < NOW  # ends near the last job, not at now
