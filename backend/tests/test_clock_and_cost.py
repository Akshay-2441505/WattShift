import time
from datetime import datetime, timezone

import pytest

from app import clock
from app.savings import bill_cost
from app.tariff import IST, TodRule

RULES = [
    TodRule("baseline", 0, 9, None, 0),
    TodRule("solar", 9, 17, "apr_sep", -15),
    TodRule("solar", 9, 17, "oct_mar", -25),
    TodRule("peak", 17, 24, None, 20),
]


def test_live_clock_is_real_time():
    clock.reset()
    assert abs((clock.now() - datetime.now(timezone.utc)).total_seconds()) < 1
    assert not clock.is_sim()


def test_sim_clock_runs_faster_and_resets():
    anchor = datetime(2026, 7, 1, 19, tzinfo=IST)
    clock.start_sim(anchor, scale=1000)
    try:
        assert clock.is_sim()
        time.sleep(0.1)
        elapsed = (clock.now() - anchor).total_seconds()
        assert 50 < elapsed < 600  # ~100 s of sim time per 0.1 s
    finally:
        clock.reset()
    assert not clock.is_sim()


def test_bill_cost_single_zone():
    # 30 min at 19:00 IST (peak +20%), 10 kW, Rs 8/kWh -> 0.5h * 10 * 8 * 1.2
    assert bill_cost(datetime(2026, 7, 1, 19, tzinfo=IST), 30, 10, RULES, 8.0) == pytest.approx(48.0)
    # 30 min at noon IST in July (solar -15%) -> 0.5 * 10 * 8 * 0.85
    assert bill_cost(datetime(2026, 7, 1, 12, tzinfo=IST), 30, 10, RULES, 8.0) == pytest.approx(34.0)


def test_bill_cost_spans_zone_boundary():
    # 16:45-17:15 IST: 15 min solar (0.85) + 15 min peak (1.2)
    expected = 0.25 * 10 * 8 * 0.85 + 0.25 * 10 * 8 * 1.2
    assert bill_cost(datetime(2026, 7, 1, 16, 45, tzinfo=IST), 30, 10, RULES, 8.0) == pytest.approx(expected)


def test_bill_cost_partial_blocks_are_prorated():
    # 10 min starting 12:10 -> 5 min in the 12:00 block, 5 in the 12:15 block; same zone
    assert bill_cost(datetime(2026, 7, 1, 12, 10, tzinfo=IST), 10, 6, RULES, 10.0) == pytest.approx(
        10 / 60 * 6 * 10 * 0.85
    )
