"""Pure window allocation: no DB, no clock. Merit-order fill of 15-min blocks under a per-block cap."""
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

BLOCK_MIN = 15
BLOCK = timedelta(minutes=BLOCK_MIN)


class NoCapacityError(Exception):
    pass


@dataclass(frozen=True)
class Block:
    start: datetime
    rate: float  # effective rate = IEX price x ToD multiplier


def jitter_minutes(job_id: str) -> int:
    """Deterministic per-job start offset (layer 1 of the anti-herd design)."""
    return int(hashlib.sha256(job_id.encode()).hexdigest(), 16) % BLOCK_MIN


def floor_block(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % BLOCK_MIN, second=0, microsecond=0)


def overlaps(start: datetime, duration_min: int) -> list[tuple[datetime, int]]:
    """[(block_start, minutes_of_job_inside_block)] for a job running [start, start+duration)."""
    end = start + timedelta(minutes=duration_min)
    b = floor_block(start)
    out = []
    while b < end:
        m = (min(end, b + BLOCK) - max(start, b)).total_seconds() / 60
        out.append((b, round(m)))
        b += BLOCK
    return out


def allocate(
    job_id: str,
    duration_min: int,
    earliest: datetime,
    deadline: datetime,
    blocks: list[Block],
    ledger: dict[datetime, int],
    cap_per_block: int,
    weight: int = 1,
) -> datetime:
    """Cheapest feasible start; caller records the consumption in its ledger. `weight` scales how much of a block's
    capacity each minute uses (e.g. the job's GPU count); the default of 1 counts plain minutes."""
    rate = {b.start: b.rate for b in blocks}
    jitter = timedelta(minutes=jitter_minutes(job_id))
    best: tuple[float, datetime] | None = None
    for b in sorted(rate):
        s = b + jitter
        if s < earliest or s + timedelta(minutes=duration_min) > deadline:
            continue
        spans = overlaps(s, duration_min)
        if any(bs not in rate or ledger.get(bs, 0) + m * weight > cap_per_block for bs, m in spans):
            continue
        cost = sum(rate[bs] * m for bs, m in spans) / duration_min
        if best is None or (cost, s) < best:
            best = (cost, s)
    if best is None:
        raise NoCapacityError(job_id)
    return best[1]
