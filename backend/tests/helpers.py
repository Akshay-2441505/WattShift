from datetime import datetime, timedelta

from app.models import PriceSignal
from app.seed import TOD_SEED
from app.tariff import IST

STEP = timedelta(minutes=15)
# Multipliers from the seeded tariff, so tests track it; test_models pins the seed itself to the MERC numbers.
PEAK_MULT = 1 + next(r.rate_adjustment_pct for r in TOD_SEED if r.zone == 'peak') / 100
SOLAR_MULT_SUMMER = 0.85  # -15% Apr-Sep; every test that uses it runs in July
# Thursday evening, peak tariff zone.
NOW = datetime(2026, 7, 1, 19, 0, tzinfo=IST)


def add_prices(session, start: datetime, rates: list[float], source: str = "iex_dam") -> None:
    session.add_all(PriceSignal(ts=start + i * STEP, price_rs_per_mwh=r, source=source) for i, r in enumerate(rates))
    session.flush()


def evening_to_next_noon(cheap_from_idx: int = 60, cheap_blocks: int = 4, cheap: float = 3000.0, dear: float = 10000.0):
    """Prices from NOW (19:00) to next-day 12:00 = 68 blocks; a cheap window starts at index 60 (10:00 next day)."""
    return [cheap if cheap_from_idx <= i < cheap_from_idx + cheap_blocks else dear for i in range(68)]
