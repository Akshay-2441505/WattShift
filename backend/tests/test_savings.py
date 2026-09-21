from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import dispatch, main, service
from app.config import settings
from app.models import Job, SavingsLog
from app.providers.fake import FakeProvider
from app.savings import record_savings, summary
from app.seed import seed_tod
from app.tariff import IST
from tests.helpers import NOW, PEAK_MULT, STEP, add_prices, evening_to_next_noon

BASE = settings.base_rate_rs_kwh
PEAK = datetime(2026, 7, 1, 19, tzinfo=IST)  # peak multiplier
SOLAR = datetime(2026, 7, 2, 12, tzinfo=IST)  # x0.85


def done_job(session, *, ran_at, baseline, dur=60, kw=10):
    j = Job(
        duration_minutes=dur, deadline=ran_at + timedelta(hours=5), provider="kaggle", status="done",
        power_kw=kw, assigned_window_start=ran_at, executed_at=ran_at, baseline_cost_rs=baseline,
    )
    session.add(j)
    session.flush()
    return j


def test_peak_to_solar_saving_is_hand_checkable(session):
    seed_tod(session)
    j = done_job(session, ran_at=SOLAR, baseline=1 * 10 * BASE * PEAK_MULT)  # baseline = run-now at peak
    row = record_savings(session, j)
    assert float(row.baseline_cost_rs) == pytest.approx(10 * BASE * PEAK_MULT)
    assert float(row.actual_cost_rs) == pytest.approx(10 * BASE * 0.85)
    assert float(row.saved_rs) == pytest.approx(10 * BASE * (PEAK_MULT - 0.85))


def test_same_zone_saves_nothing(session):
    seed_tod(session)
    j = done_job(session, ran_at=PEAK, baseline=10 * BASE * PEAK_MULT)
    assert float(record_savings(session, j).saved_rs) == pytest.approx(0)


def test_negative_saving_is_kept_not_clamped(session):
    seed_tod(session)
    j = done_job(session, ran_at=PEAK, baseline=10 * BASE * 0.85)  # would have been cheaper to run at once
    assert float(record_savings(session, j).saved_rs) == pytest.approx(10 * BASE * (0.85 - PEAK_MULT))


def test_record_is_idempotent(session):
    seed_tod(session)
    j = done_job(session, ran_at=SOLAR, baseline=100)
    record_savings(session, j)
    record_savings(session, j)
    assert session.query(SavingsLog).count() == 1


def test_pipeline_saving_equals_baseline_minus_planned_when_fired_on_time(session):
    """submit -> fire at the assigned window -> Kaggle done -> savings row."""
    seed_tod(session)
    add_prices(session, NOW, evening_to_next_noon())
    job = service.submit_job(session, 60, NOW + timedelta(hours=17), "kaggle", now=NOW, cap_per_block=15)
    fake = FakeProvider()
    dispatch.fire_due_jobs(session, {"kaggle": fake}, now=job.assigned_window_start)
    fake.statuses[f"fake/{job.id}"] = "done"
    dispatch.poll_running(session, {"kaggle": fake})
    row = session.get(SavingsLog, job.id)
    assert row is not None
    assert float(row.saved_rs) == pytest.approx(float(job.baseline_cost_rs) - float(job.planned_cost_rs))
    assert float(row.saved_rs) > 0


def test_a_late_start_shows_the_lost_saving(session):
    """Planned for solar but the service was down and it only ran at peak: the log says so honestly."""
    seed_tod(session)
    add_prices(session, NOW, evening_to_next_noon())
    job = service.submit_job(session, 60, NOW + timedelta(hours=20), "kaggle", now=NOW, cap_per_block=15)
    fake = FakeProvider()
    late = NOW + timedelta(hours=1)  # 20:00 IST, peak — well before the solar window; force an early run
    job.assigned_window_start = late
    session.flush()
    dispatch.fire_due_jobs(session, {"kaggle": fake}, now=late)
    fake.statuses[f"fake/{job.id}"] = "done"
    dispatch.poll_running(session, {"kaggle": fake})
    assert float(session.get(SavingsLog, job.id).saved_rs) == pytest.approx(0)  # same peak zone as baseline


def test_failed_jobs_write_no_savings_row(session):
    seed_tod(session)
    j = Job(duration_minutes=15, deadline=NOW + timedelta(hours=5), provider="kaggle", status="running",
            external_ref="r/x", executed_at=NOW, baseline_cost_rs=50)
    session.add(j)
    session.flush()
    fake = FakeProvider()
    fake.statuses["r/x"] = "failed"
    dispatch.poll_running(session, {"kaggle": fake})
    assert session.query(SavingsLog).count() == 0


def test_summary_buckets_by_ist_day_and_zero_fills(session):
    seed_tod(session)
    today = datetime(2026, 7, 10, 12, tzinfo=IST)
    # (days ago, baseline, so that saved = baseline - actual(solar 1h,10kW))
    actual = 10 * BASE * 0.85
    for days_ago, saved in [(0, 30.0), (0, 20.0), (2, 10.0), (9, 100.0)]:  # 9 days ago is outside the 7-day window
        j = done_job(session, ran_at=today - timedelta(days=days_ago), baseline=actual + saved)
        record_savings(session, j)
    s = summary(session, today)
    assert s["total"] == pytest.approx(160.0)
    assert s["today"] == pytest.approx(50.0)
    assert s["week"] == pytest.approx(60.0)
    assert s["daily_avg"] == pytest.approx(60.0 / 7, abs=0.01)
    assert [d["date"] for d in s["daily"]][-1] == "2026-07-10" and len(s["daily"]) == 7
    assert s["daily"][-3]["saved"] == pytest.approx(10.0)
    assert s["daily"][0]["saved"] == 0  # zero-filled day with no jobs
    assert s["jobs_counted"] == 4
    assert s["pct_saved"] == pytest.approx(160 / (4 * actual + 160) * 100, abs=0.01)


def test_summary_of_nothing_is_zeros_not_an_error(session):
    s = summary(session, NOW)
    assert s["total"] == 0 and s["today"] == 0 and s["jobs_counted"] == 0 and s["pct_saved"] is None
    assert len(s["daily"]) == 7


def test_summary_endpoint(session):
    seed_tod(session)
    record_savings(session, done_job(session, ran_at=SOLAR, baseline=10 * BASE * PEAK_MULT))
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: SOLAR
    try:
        r = TestClient(main.app).get("/savings/summary")
    finally:
        main.app.dependency_overrides.clear()
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == pytest.approx(10 * BASE * (PEAK_MULT - 0.85), abs=0.01)
    assert set(body) >= {"total", "today", "week", "daily_avg", "daily", "jobs_counted", "pct_saved", "baseline_total"}
