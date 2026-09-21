from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))  # India has no DST; avoids a tzdata dependency


@dataclass(frozen=True)
class TodRule:
    zone: str  # 'baseline' | 'solar' | 'peak'
    start_hour: int  # inclusive, IST
    end_hour: int  # exclusive, 24 = midnight
    season: str | None  # 'apr_sep' | 'oct_mar' | None (year-round)
    rate_adjustment_pct: float


def season_of(ts_ist: datetime) -> str:
    return "apr_sep" if 4 <= ts_ist.month <= 9 else "oct_mar"


def tod_zone(ts: datetime, rules: list[TodRule]) -> TodRule:
    if ts.tzinfo is None:
        raise ValueError("naive datetime; pass a tz-aware timestamp")
    t = ts.astimezone(IST)
    season = season_of(t)
    for r in rules:
        if r.start_hour <= t.hour < r.end_hour and r.season in (None, season):
            return r
    raise LookupError(f"no ToD rule covers {t.isoformat()}")


def tod_multiplier(ts: datetime, rules: list[TodRule]) -> float:
    return 1 + tod_zone(ts, rules).rate_adjustment_pct / 100
