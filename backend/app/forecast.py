from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.allocator import floor_block
from app.config import CARBON_PROXY, settings
from app.models import PriceSignal
from app.seed import load_rules
from app.service import active_source
from app.tariff import IST, tod_zone


def build_forecast(session: Session, now: datetime, hours: int = 24) -> dict:
    """Per-15-min price/tariff/carbon curve for the next `hours`, only for blocks that actually have prices."""
    rules = load_rules(session)
    first = floor_block(now)
    source = active_source()
    rows = session.execute(
        select(PriceSignal.ts, PriceSignal.price_rs_per_mwh)
        .where(PriceSignal.source == source, PriceSignal.ts >= first, PriceSignal.ts < first + timedelta(hours=hours))
        .order_by(PriceSignal.ts)
    ).all()
    blocks = []
    for ts, price in rows:
        rule = tod_zone(ts, rules)
        mult = 1 + float(rule.rate_adjustment_pct) / 100
        blocks.append(
            {
                "ts": ts,
                "iex_price": float(price),  # Rs/MWh
                "tod_zone": rule.zone,
                "tod_multiplier": mult,
                "billed_rs_kwh": settings.base_rate_rs_kwh * mult,  # what the DISCOM bill charges
                "effective_rate": float(price) * mult,  # what the scheduler ranks on
                "carbon_index": CARBON_PROXY[ts.astimezone(IST).hour],
            }
        )
    return {
        "now": now,
        "mode": "replay" if clock.is_sim() else "live",
        "sim_scale": clock.scale(),
        "source": source,
        "carbon_modeled": True,
        "blocks": blocks,
    }
