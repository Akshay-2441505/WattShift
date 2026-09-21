"""The time-lapse tariff for the five-minute demo. Applied in the demo cloud's process ONLY (see demo_cloud_main.py):
every PERIOD_S seconds counts as one tariff "hour", so the cheap window is minutes away instead of up to an hour. The real
tariff, planner, measurement and sync code run unchanged on top of these patches, and the demo says it is a time-lapse."""
from datetime import datetime, timedelta

PERIOD_S = 120  # one pseudo-hour
MIN_LEAD_S = 230  # before the cheap window opens: the cloud plans about 30-70 s in and will not set a start time closer than its
# 120 s start margin (a shorter lead let one run finish with nothing deferred), and the jobs' own start must be well before it


def pseudo_hour(ts: datetime) -> int:
    return int(ts.timestamp() // PERIOD_S) % 24


def choose_boundary(now: datetime, min_lead_s: int = MIN_LEAD_S) -> datetime:
    """The next period edge that is at least min_lead_s away: the moment the demo tariff turns cheap."""
    edge = (int(now.timestamp()) // PERIOD_S + 1) * PERIOD_S
    while edge - now.timestamp() < min_lead_s:
        edge += PERIOD_S
    return datetime.fromtimestamp(edge, now.tzinfo)


def peak_pseudo_hours(now: datetime, boundary: datetime) -> set[int]:
    """The pseudo-hours from now up to the boundary are expensive; every other one is cheap."""
    first = int(now.timestamp()) // PERIOD_S
    last = int(boundary.timestamp()) // PERIOD_S  # exclusive
    return {h % 24 for h in range(first, last)}


def tod_zone(ts: datetime, rules):
    """Replaces app.tariff.tod_zone: the zone is looked up by pseudo-hour instead of the IST clock hour."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime; pass a tz-aware timestamp")
    h = pseudo_hour(ts)
    for r in rules:
        if r.start_hour <= h < r.end_hour:
            return r
    raise LookupError(f"no ToD rule covers pseudo-hour {h}")


def apply() -> None:
    from app import agent_sync, allocator, planner, tariff

    allocator.BLOCK_MIN = 1  # 1-minute blocks (floor_block and overlaps read these at call time)
    allocator.BLOCK = timedelta(minutes=1)
    planner.BLOCK_MIN = 1  # planner imported the number by value, so it needs its own patch
    # (the per-job start-time spread is `hash % BLOCK_MIN`, so with 1-minute blocks it is 0 without a separate patch)
    tariff.tod_zone = tod_zone  # tod_multiplier and the dashboard's zone bands call it through the module
    agent_sync.POLL_SECONDS = 10  # the agent looks every 10 s in the demo
