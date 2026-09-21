from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Job, PriceSignal, TodScheduleRow
from app.seed import load_rules, seed_tod
from app.tariff import IST, TodRule, tod_multiplier
from tests.helpers import PEAK_MULT

NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def test_seed_is_idempotent_and_round_trips(session):
    seed_tod(session)
    seed_tod(session)
    assert session.query(TodScheduleRow).count() == 4
    rules = load_rules(session)
    assert tod_multiplier(datetime(2026, 7, 1, 12, tzinfo=IST), rules) == pytest.approx(0.85)
    assert tod_multiplier(datetime(2026, 7, 1, 19, tzinfo=IST), rules) == pytest.approx(PEAK_MULT)


def test_seed_is_pinned_to_the_verified_merc_tariff():
    """MERC Case No. 75 of 2025 (25 Mar 2026), Table 8, HT Industrial, FY 2025-26 and FY 2026-27. If this fails after a
    1 April tariff change, update seed.py from the new order, not this test."""
    from app.seed import TOD_SEED

    assert TOD_SEED == [
        TodRule("baseline", 0, 9, None, 0),  # night rebate removed by the review order (s.18.13)
        TodRule("solar", 9, 17, "apr_sep", -15),
        TodRule("solar", 9, 17, "oct_mar", -25),
        TodRule("peak", 17, 24, None, 25),  # LT/HT Industrial & Commercial, per the Case 217 of 2024 press note
    ]
    from app.config import settings

    assert settings.base_rate_rs_kwh == 8.44  # HT I(A) energy charge FY 2026-27, order p.100


def test_price_signal_unique_per_ts_and_source(session):
    session.add(PriceSignal(ts=NOW, price_rs_per_mwh=4000))
    session.flush()
    session.add(PriceSignal(ts=NOW, price_rs_per_mwh=4100))
    with pytest.raises(IntegrityError):
        session.flush()


def test_job_defaults(session):
    j = Job(duration_minutes=30, deadline=NOW + timedelta(hours=5), provider="kaggle")
    session.add(j)
    session.flush()
    session.refresh(j)
    assert j.id is not None
    assert j.status == "queued"
    assert j.submitted_at is not None
    assert float(j.power_kw) > 0


@pytest.mark.parametrize(
    "kw",
    [
        {"duration_minutes": 0},
        {"provider": "runpod"},
        {"status": "weird"},
    ],
)
def test_job_constraints(session, kw):
    base = dict(duration_minutes=30, deadline=NOW + timedelta(hours=5), provider="kaggle")
    session.add(Job(**{**base, **kw}))
    with pytest.raises(IntegrityError):
        session.flush()
