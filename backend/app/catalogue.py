"""Tariff catalogue: one verified, versioned, expiring entry per utility and category (spec section 11)."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import TariffCatalogue
from app.seed import TOD_SEED
from app.tariff import TodRule

MSEDCL_HT_IA_FROM = date(2026, 4, 1)  # FY 2026-27 starts here; expiry comes from settings.tariff_valid_until


@dataclass(frozen=True)
class Tariff:
    utility: str
    category: str
    rules: list[TodRule]
    base_rate: float  # Rs/kWh energy charge
    verified: bool
    valid_until: date
    source: str


def _rules_to_json(rules: list[TodRule]) -> list[dict]:
    return [
        {"zone": r.zone, "start_hour": r.start_hour, "end_hour": r.end_hour, "season": r.season, "adj_pct": r.rate_adjustment_pct}
        for r in rules
    ]


def _rules_from_json(rows: list[dict]) -> list[TodRule]:
    return [TodRule(r["zone"], r["start_hour"], r["end_hour"], r["season"], float(r["adj_pct"])) for r in rows]


def seed_catalogue(session: Session) -> None:
    """Insert or refresh the verified MSEDCL HT-I(A) FY 2026-27 entry from seed.py and settings. Safe to re-run."""
    values = dict(
        valid_until=settings.tariff_valid_until, rules=_rules_to_json(TOD_SEED), base_rate=settings.base_rate_rs_kwh,
        verified=settings.tariff_verified, source=settings.tariff_source,
    )
    row = session.scalar(
        select(TariffCatalogue).where(
            TariffCatalogue.utility == "MSEDCL", TariffCatalogue.category == "HT-I(A)",
            TariffCatalogue.valid_from == MSEDCL_HT_IA_FROM,
        )
    )
    if row is None:
        session.add(TariffCatalogue(utility="MSEDCL", category="HT-I(A)", valid_from=MSEDCL_HT_IA_FROM, **values))
    else:
        for k, v in values.items():
            setattr(row, k, v)
    session.flush()


def get_tariff(session: Session, utility: str, category: str, on: date) -> Tariff:
    """The entry valid on `on` (an IST calendar date). Raises LookupError if none is: callers then plan nothing."""
    row = session.scalar(
        select(TariffCatalogue)
        .where(
            TariffCatalogue.utility == utility, TariffCatalogue.category == category,
            TariffCatalogue.valid_from <= on, TariffCatalogue.valid_until >= on,
        )
        .order_by(TariffCatalogue.valid_from.desc())
    )
    if row is None:
        raise LookupError(f"no tariff for {utility} {category} on {on}")
    return Tariff(row.utility, row.category, _rules_from_json(row.rules), float(row.base_rate), row.verified, row.valid_until, row.source)
