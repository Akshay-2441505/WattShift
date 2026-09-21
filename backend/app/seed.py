from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import TodScheduleRow
from app.tariff import TodRule

# MSEDCL ToD for HT Industrial, FY 2025-26 and FY 2026-27, as a % of the energy charge. Verified 2026-09-19 against
# MERC Case No. 75 of 2025 (order dated 25 Mar 2026): Table 8 (pp.76-77) gives solar 09-17 at -15% (Apr-Sep) / -25%
# (Oct-Mar) and 0% for 00-09; s.18.13 removed the night rebate that the 28 Mar 2025 order (Case 217 of 2024) had
# granted for 00-06. Peak 17-24 is +25% for LT/HT Industrial & Commercial per the Case 217 press note; Table 8 shows
# "+20%#" with the footnote text missing from the PDF, so that one number rests on the earlier order.
# STALE FROM 1 APR 2027: solar becomes -20% / -30% and the HT-I(A) energy charge Rs 8.23 (settings.tariff_valid_until).
TOD_SEED = [
    TodRule("baseline", 0, 9, None, 0),
    TodRule("solar", 9, 17, "apr_sep", -15),
    TodRule("solar", 9, 17, "oct_mar", -25),
    TodRule("peak", 17, 24, None, 25),
]


def seed_tod(session: Session, rules: list[TodRule] = TOD_SEED) -> None:
    """Replace the tariff table wholesale, so re-running (or correcting numbers) is safe."""
    session.execute(delete(TodScheduleRow))
    session.add_all(
        TodScheduleRow(
            zone_name=r.zone, start_hour=r.start_hour, end_hour=r.end_hour,
            season=r.season, rate_adjustment_pct=r.rate_adjustment_pct,
        )
        for r in rules
    )
    session.flush()


def load_rules(session: Session) -> list[TodRule]:
    rows = session.scalars(select(TodScheduleRow).order_by(TodScheduleRow.id))
    return [TodRule(r.zone_name, r.start_hour, r.end_hour, r.season, float(r.rate_adjustment_pct)) for r in rows]
