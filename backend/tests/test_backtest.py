from datetime import date, datetime, timedelta

import pytest

from app.backtest import (
    Assumptions,
    BacktestInputError,
    TraceJob,
    read_jobs_csv,
    run_backtest,
)
from app.seed import TOD_SEED
from app.tariff import IST
from tests.helpers import PEAK_MULT

STEP = timedelta(minutes=15)
D1 = datetime(2026, 8, 17, tzinfo=IST)  # a Monday; every day below is in FY 2026-27, Apr-Sep season (solar -15%)
BASE = 8.44


def prices(days=3, cheap=(10, 14), cheap_rate=3000.0, dear=10000.0):
    """96 blocks/day; IEX price is `cheap_rate` between the given IST hours, `dear` otherwise."""
    out = {}
    for d in range(days):
        for i in range(96):
            ts = D1 + timedelta(days=d) + i * STEP
            out[ts] = cheap_rate if cheap[0] <= ts.hour < cheap[1] else dear
    return out


def A(**kw):
    base = dict(flexible_share=1.0, slack_hours=24, kw_per_gpu=1.25, cluster_gpus=10_000, shift_capacity_share=1.0, base_rate_rs_kwh=BASE)
    return Assumptions(**{**base, **kw})


def job(jid="j1", hour=19, dur=60, gpus=8, day=0):
    return TraceJob(jid, D1 + timedelta(days=day, hours=hour), dur, gpus)


def test_nothing_flexible_means_nothing_saved():
    r = run_backtest([job(f"j{i}") for i in range(20)], prices(), TOD_SEED, A(flexible_share=0.0))
    assert r.saved_rs == 0 and r.scheduled_rs == r.baseline_rs
    assert r.n_flexible == 0 and r.n_placed == 0


def test_an_evening_job_moves_to_solar_hours_and_the_saving_is_hand_checkable():
    r = run_backtest([job(hour=19, dur=60, gpus=8)], prices(), TOD_SEED, A())
    kw = 8 * 1.25  # 10 kW for one hour
    assert r.baseline_rs == pytest.approx(kw * BASE * PEAK_MULT)  # run at 19:00 = peak
    assert r.scheduled_rs == pytest.approx(kw * BASE * 0.85, rel=0.02)  # lands wholly in a solar block
    assert r.saved_rs == pytest.approx(kw * BASE * (PEAK_MULT - 0.85), rel=0.02)
    assert r.n_placed == 1 and r.avg_delay_hours > 10  # waited for tomorrow's midday


def test_long_jobs_are_never_shifted():
    r = run_backtest([job(dur=240)], prices(), TOD_SEED, A(max_shiftable_min=180))
    assert r.n_eligible == 0 and r.n_placed == 0 and r.saved_rs == 0


def test_short_slack_cannot_reach_the_cheap_hours():
    r = run_backtest([job(hour=19, dur=60)], prices(), TOD_SEED, A(slack_hours=1))
    assert r.saved_rs == pytest.approx(0, abs=0.01)  # still peak inside a one-hour wait


def test_capacity_limits_how_much_shifted_work_lands_in_one_block():
    jobs = [job(f"j{i}", hour=19, dur=60, gpus=8) for i in range(6)]
    tight = run_backtest(jobs, prices(), TOD_SEED, A(cluster_gpus=16, shift_capacity_share=1.0))  # 16 GPUs per block
    loose = run_backtest(jobs, prices(), TOD_SEED, A(cluster_gpus=10_000))
    assert tight.max_block_gpus <= 16
    assert loose.max_block_gpus > tight.max_block_gpus  # without the cap they pile into the cheapest hour
    assert tight.saved_rs < loose.saved_rs + 1e-6  # spreading can only cost savings, never invent them


def test_the_flexible_subset_is_stable_and_close_to_the_requested_share():
    jobs = [job(f"job-{i}", hour=8 + i % 10, dur=30, gpus=1) for i in range(2000)]
    a = run_backtest(jobs, prices(), TOD_SEED, A(flexible_share=0.3))
    b = run_backtest(jobs, prices(), TOD_SEED, A(flexible_share=0.3))
    assert a.n_flexible == b.n_flexible and a.saved_rs == b.saved_rs  # deterministic
    assert 0.26 * 2000 < a.n_flexible < 0.34 * 2000


def test_energy_by_zone_shows_the_peak_share_falling():
    r = run_backtest([job(hour=19, dur=60, gpus=8)], prices(), TOD_SEED, A())
    assert r.kwh_by_zone_before["peak"] == pytest.approx(10.0)
    assert r.kwh_by_zone_after["peak"] == pytest.approx(0.0, abs=0.01)
    assert sum(r.kwh_by_zone_before.values()) == pytest.approx(sum(r.kwh_by_zone_after.values()))  # energy is conserved
    assert len(r.hourly_kwh_before) == 24 and r.hourly_kwh_before[19] == pytest.approx(10.0)


def test_jobs_near_the_end_of_the_price_data_do_not_crash_and_are_reported():
    late = TraceJob("late", D1 + timedelta(days=2, hours=22), 60, 8)  # deadline runs past the last price block
    r = run_backtest([late], prices(days=3), TOD_SEED, A())
    assert r.n_jobs == 1 and (r.n_placed + r.n_unplaced) == 1


def test_a_job_with_no_prices_at_all_runs_at_submit_and_is_counted_unplaced():
    r = run_backtest([job()], {}, TOD_SEED, A())
    assert r.n_unplaced == 1 and r.saved_rs == 0


def test_daily_savings_are_bucketed_by_submit_day_ist():
    r = run_backtest([job("a", day=0), job("b", day=1)], prices(), TOD_SEED, A())
    days = {d["date"]: d["saved"] for d in r.daily}
    assert set(days) == {"2026-08-17", "2026-08-18"} and all(v > 0 for v in days.values())


def test_sensitivity_grid_shows_savings_growing_with_flexibility_and_slack():
    from app.backtest import sensitivity

    jobs = [job(f"job-{i}", hour=17 + i % 6, dur=45, gpus=8) for i in range(300)]
    grid = sensitivity(jobs, prices(), TOD_SEED, A(), shares=(0.25, 1.0), slacks=(2, 24))
    by = {(g["flexible_share"], g["slack_hours"]): g["pct_saved"] for g in grid}
    assert len(grid) == 4
    assert by[(1.0, 24)] > by[(0.25, 24)] > 0  # more flexible jobs -> more saved
    assert by[(1.0, 24)] > by[(1.0, 2)]  # a longer wait can reach the cheap hours
    assert by[(0.25, 2)] <= by[(1.0, 24)]


def test_assumptions_are_validated():
    for bad in (dict(flexible_share=1.5), dict(slack_hours=-1), dict(kw_per_gpu=0), dict(cluster_gpus=0), dict(shift_capacity_share=0)):
        with pytest.raises(ValueError):
            run_backtest([job()], prices(), TOD_SEED, A(**bad))


def test_energy_by_job_length_shows_where_the_electricity_goes():
    from app.backtest import energy_by_length

    jobs = [job("a", dur=10, gpus=1)] * 9 + [job("b", dur=60 * 30, gpus=8)]  # nine tiny jobs, one 30-hour 8-GPU job
    rows = energy_by_length(jobs, kw_per_gpu=1.25)
    by = {r["label"]: r for r in rows}
    assert [r["label"] for r in rows] == ["Under 15 min", "15 min to 1 h", "1 to 3 h", "3 to 12 h", "12 to 24 h", "Over 24 h"]
    assert by["Under 15 min"]["jobs_share"] == pytest.approx(0.9)
    assert by["Over 24 h"]["jobs_share"] == pytest.approx(0.1)
    assert by["Over 24 h"]["energy_share"] > 0.99  # the one long job is almost all the energy
    assert sum(r["jobs_share"] for r in rows) == pytest.approx(1) and sum(r["energy_share"] for r in rows) == pytest.approx(1)


def test_retime_weeks_keeps_weekday_and_time_of_day():
    from app.backtest import retime_weeks

    old = [TraceJob("x", datetime(2020, 6, 3, 14, 30, tzinfo=IST), 30, 1), TraceJob("y", datetime(2020, 6, 9, 2, 0, tzinfo=IST), 30, 1)]
    new = retime_weeks(old, datetime(2026, 8, 17, tzinfo=IST))  # a Monday
    assert new[0].submit == datetime(2026, 8, 19, 14, 30, tzinfo=IST)  # Wednesday stays Wednesday
    assert new[1].submit == datetime(2026, 8, 25, 2, 0, tzinfo=IST)  # spacing between jobs unchanged
    assert new[0].submit.weekday() == old[0].submit.weekday()


# --- CSV import -----------------------------------------------------------------------------------------------

GOOD = "job_id,submit_time,duration_minutes,gpus\na,2026-08-17 19:00:00,60,8\nb,2026-08-18T02:30:00+05:30,15.5,1\n"


def test_reads_a_valid_file_treating_naive_times_as_ist():
    jobs = read_jobs_csv(GOOD)
    assert [j.job_id for j in jobs] == ["a", "b"]
    assert jobs[0].submit == datetime(2026, 8, 17, 19, tzinfo=IST)
    assert jobs[1].submit == datetime(2026, 8, 18, 2, 30, tzinfo=IST) and jobs[1].duration_min == 15.5 and jobs[1].gpus == 1


def test_missing_columns_are_named():
    with pytest.raises(BacktestInputError) as e:
        read_jobs_csv("job_id,submit_time\na,2026-08-17 19:00:00\n")
    assert "duration_minutes" in str(e.value) and "gpus" in str(e.value)


def test_bad_rows_are_reported_with_their_line_numbers_all_at_once():
    text = "job_id,submit_time,duration_minutes,gpus\na,not-a-date,60,8\nb,2026-08-17 19:00:00,0,8\nc,2026-08-17 19:00:00,60,0\nd,2026-08-17 19:00:00,60,8\n"
    with pytest.raises(BacktestInputError) as e:
        read_jobs_csv(text)
    msg = str(e.value)
    assert "line 2" in msg and "line 3" in msg and "line 4" in msg and "line 5" not in msg


def test_an_empty_file_is_an_error():
    with pytest.raises(BacktestInputError):
        read_jobs_csv("job_id,submit_time,duration_minutes,gpus\n")


def test_too_many_rows_is_refused():
    rows = "\n".join(f"j{i},2026-08-17 19:00:00,10,1" for i in range(101))
    with pytest.raises(BacktestInputError, match="100"):
        read_jobs_csv("job_id,submit_time,duration_minutes,gpus\n" + rows, max_rows=100)
