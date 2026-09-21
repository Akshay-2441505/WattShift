from datetime import datetime, timezone

import pytest

from app.tariff import IST, TodRule, tod_multiplier, tod_zone

# Numbers per SYSTEM_DESIGN.md §5 — unverified against the MERC order (plan Phase 0.3).
RULES = [
    TodRule("baseline", 0, 9, None, 0),
    TodRule("solar", 9, 17, "apr_sep", -15),
    TodRule("solar", 9, 17, "oct_mar", -25),
    TodRule("peak", 17, 24, None, 20),
]


def ist(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=IST)


@pytest.mark.parametrize(
    "ts, zone, mult",
    [
        (ist(7, 1, 0), "baseline", 1.0),
        (ist(7, 1, 8, 59), "baseline", 1.0),
        (ist(7, 1, 9), "solar", 0.85),
        (ist(7, 1, 16, 59), "solar", 0.85),
        (ist(7, 1, 17), "peak", 1.2),
        (ist(7, 1, 23, 59), "peak", 1.2),
        (ist(12, 1, 12), "solar", 0.75),  # winter season is deeper
    ],
)
def test_zone_and_multiplier(ts, zone, mult):
    assert tod_zone(ts, RULES).zone == zone
    assert tod_multiplier(ts, RULES) == pytest.approx(mult)


def test_season_boundary():
    assert tod_multiplier(ist(9, 30, 12), RULES) == pytest.approx(0.85)
    assert tod_multiplier(ist(10, 1, 12), RULES) == pytest.approx(0.75)
    assert tod_multiplier(ist(3, 31, 12), RULES) == pytest.approx(0.75)
    assert tod_multiplier(ist(4, 1, 12), RULES) == pytest.approx(0.85)


def test_utc_input_is_converted_to_ist():
    # 03:30 UTC == 09:00 IST -> solar starts
    assert tod_zone(datetime(2026, 7, 1, 3, 30, tzinfo=timezone.utc), RULES).zone == "solar"
    assert tod_zone(datetime(2026, 7, 1, 3, 29, tzinfo=timezone.utc), RULES).zone == "baseline"


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        tod_multiplier(datetime(2026, 7, 1, 12), RULES)
