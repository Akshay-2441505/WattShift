from datetime import date, datetime

import pytest

from app.catalogue import get_tariff, seed_catalogue
from app.config import settings
from app.models import TariffCatalogue
from app.seed import TOD_SEED
from app.tariff import IST, tod_multiplier


def test_seeded_entry_matches_the_verified_tariff(session):
    seed_catalogue(session)
    t = get_tariff(session, "MSEDCL", "HT-I(A)", date(2026, 9, 20))
    assert t.rules == TOD_SEED
    assert t.base_rate == 8.44 and t.verified and t.valid_until == settings.tariff_valid_until
    assert tod_multiplier(datetime(2026, 7, 1, 19, tzinfo=IST), t.rules) == pytest.approx(1.25)


def test_seeding_twice_keeps_one_row(session):
    seed_catalogue(session)
    seed_catalogue(session)
    assert session.query(TariffCatalogue).count() == 1


@pytest.mark.parametrize("on", [date(2026, 3, 31), date(2027, 4, 1)])  # the day before it starts; the day after it expires
def test_outside_validity_is_an_error(session, on):
    seed_catalogue(session)
    with pytest.raises(LookupError):
        get_tariff(session, "MSEDCL", "HT-I(A)", on)


def test_last_valid_day_is_included(session):
    seed_catalogue(session)
    assert get_tariff(session, "MSEDCL", "HT-I(A)", settings.tariff_valid_until).valid_until == settings.tariff_valid_until


def test_unknown_category_is_an_error(session):
    seed_catalogue(session)
    with pytest.raises(LookupError):
        get_tariff(session, "MSEDCL", "LT-V", date(2026, 9, 20))
