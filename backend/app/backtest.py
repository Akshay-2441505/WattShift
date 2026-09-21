"""'What would you have saved?': replay a company's PAST jobs against real IEX prices and the official tariff.

Pure and offline: no database, no clock, nothing is run. Every job is billed twice under the ToD tariff:
  baseline  = it ran the moment it was submitted;
  scheduled = a job that can wait was held for the cheapest window before its deadline (same allocator as the product).
Jobs longer than `max_shiftable_min` cannot be shifted and cost the same in both worlds, so the result honestly
shows how much of the energy bill timing can actually touch.

Assumptions a file cannot tell us (which jobs are flexible, how long they may wait, power per GPU, spare capacity)
are explicit parameters, never hidden constants.
"""
import csv
import hashlib
import io
import math
from bisect import bisect_left
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from app.allocator import BLOCK_MIN, Block, NoCapacityError, allocate, floor_block, overlaps
from app.savings import bill_cost
from app.tariff import IST, TodRule, tod_multiplier, tod_zone


class BacktestInputError(ValueError):
    """The uploaded file or assumptions are unusable; carries every problem found, not just the first."""

    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


@dataclass(frozen=True)
class TraceJob:
    job_id: str
    submit: datetime  # tz-aware
    duration_min: float
    gpus: int


@dataclass(frozen=True)
class Assumptions:
    flexible_share: float = 0.5  # share of shiftable jobs that are allowed to wait (the file cannot tell us)
    slack_hours: float = 12.0  # how long a flexible job may wait beyond its own run time
    kw_per_gpu: float = 1.25  # ~ one 8-GPU node at 10 kW, cooling included; modeled, not measured
    cluster_gpus: int = 1000  # size of the fleet, for the capacity limit
    shift_capacity_share: float = 0.5  # at most this share of the fleet may run shifted work in any 15-min block
    max_shiftable_min: int = 180  # longer jobs run at once (same cap as the product)
    base_rate_rs_kwh: float = 8.44  # MSEDCL HT-I(A) energy charge, FY 2026-27


@dataclass
class BacktestResult:
    n_jobs: int = 0
    n_eligible: int = 0  # short enough to shift
    n_flexible: int = 0  # eligible AND assumed able to wait
    n_placed: int = 0  # flexible jobs the allocator found a window for
    n_unplaced: int = 0  # flexible jobs with no priced window with spare capacity; they ran at submission
    kwh_total: float = 0.0
    kwh_flexible: float = 0.0
    baseline_rs: float = 0.0
    scheduled_rs: float = 0.0
    saved_rs: float = 0.0
    pct_saved: float | None = None
    avg_delay_hours: float = 0.0
    max_block_gpus: float = 0.0  # most shifted GPUs sharing any one 15-min block
    kwh_by_zone_before: dict[str, float] = field(default_factory=dict)
    kwh_by_zone_after: dict[str, float] = field(default_factory=dict)
    hourly_kwh_before: list[float] = field(default_factory=lambda: [0.0] * 24)  # by IST hour of day
    hourly_kwh_after: list[float] = field(default_factory=lambda: [0.0] * 24)
    daily: list[dict] = field(default_factory=list)  # {date, saved}, by IST submit day

    @property
    def flexible_energy_share(self) -> float:
        return self.kwh_flexible / self.kwh_total if self.kwh_total else 0.0


def _validate(a: Assumptions) -> None:
    bad = []
    if not 0 <= a.flexible_share <= 1:
        bad.append("flexible_share must be between 0 and 1")
    if a.slack_hours < 0:
        bad.append("slack_hours cannot be negative")
    if a.kw_per_gpu <= 0:
        bad.append("kw_per_gpu must be above 0")
    if a.cluster_gpus < 1:
        bad.append("cluster_gpus must be at least 1")
    if not 0 < a.shift_capacity_share <= 1:
        bad.append("shift_capacity_share must be above 0 and at most 1")
    if bad:
        raise ValueError("; ".join(bad))


def _is_flexible(job_id: str, share: float) -> bool:
    """Stable pick by id, so the same file and assumptions always give the same answer."""
    return int(hashlib.sha256(job_id.encode()).hexdigest(), 16) % 10_000 < share * 10_000


def _energy(result_zone: dict, hourly: list, start: datetime, dur: int, kw: float, rules: list[TodRule]) -> None:
    for block, minutes in overlaps(start, dur):
        kwh = kw * minutes / 60
        zone = tod_zone(block, rules).zone
        result_zone[zone] = result_zone.get(zone, 0.0) + kwh
        hourly[block.astimezone(IST).hour] += kwh


def run_backtest(jobs: list[TraceJob], prices: dict[datetime, float], rules: list[TodRule], a: Assumptions) -> BacktestResult:
    """`prices`: IEX day-ahead price (Rs/MWh) by 15-min block start."""
    _validate(a)
    blocks = sorted((Block(ts, p * tod_multiplier(ts, rules)) for ts, p in prices.items()), key=lambda b: b.start)
    starts = [b.start for b in blocks]
    cap = a.shift_capacity_share * a.cluster_gpus * BLOCK_MIN  # GPU-minutes per block
    ledger: dict[datetime, int] = {}
    zones = {rule.zone: 0.0 for rule in rules}  # every zone always present, even at zero
    r = BacktestResult(n_jobs=len(jobs), kwh_by_zone_before=dict(zones), kwh_by_zone_after=dict(zones))
    daily: dict[str, float] = {}
    delays: list[float] = []

    for j in sorted(jobs, key=lambda x: (x.submit, x.job_id)):
        dur = max(1, math.ceil(j.duration_min))
        kw = j.gpus * a.kw_per_gpu
        base = bill_cost(j.submit, dur, kw, rules, a.base_rate_rs_kwh)
        start = j.submit
        eligible = dur <= a.max_shiftable_min
        flexible = eligible and _is_flexible(j.job_id, a.flexible_share)
        r.n_eligible += eligible
        r.n_flexible += flexible
        r.kwh_total += kw * dur / 60
        if flexible:
            r.kwh_flexible += kw * dur / 60
            deadline = j.submit + timedelta(minutes=dur) + timedelta(hours=a.slack_hours)
            window = blocks[bisect_left(starts, floor_block(j.submit)) : bisect_left(starts, deadline)]
            try:
                start = allocate(j.job_id, dur, j.submit, deadline, window, ledger, int(cap), weight=j.gpus)
            except NoCapacityError:
                r.n_unplaced += 1
                start = j.submit
            else:
                r.n_placed += 1
                delays.append((start - j.submit).total_seconds() / 3600)
                for block, minutes in overlaps(start, dur):
                    ledger[block] = ledger.get(block, 0) + minutes * j.gpus
        actual = bill_cost(start, dur, kw, rules, a.base_rate_rs_kwh)
        r.baseline_rs += base
        r.scheduled_rs += actual
        day = j.submit.astimezone(IST).date().isoformat()
        daily[day] = daily.get(day, 0.0) + (base - actual)
        _energy(r.kwh_by_zone_before, r.hourly_kwh_before, j.submit, dur, kw, rules)
        _energy(r.kwh_by_zone_after, r.hourly_kwh_after, start, dur, kw, rules)

    r.saved_rs = r.baseline_rs - r.scheduled_rs
    r.pct_saved = r.saved_rs / r.baseline_rs * 100 if r.baseline_rs else None
    r.avg_delay_hours = sum(delays) / len(delays) if delays else 0.0
    r.max_block_gpus = max(ledger.values(), default=0) / BLOCK_MIN
    r.daily = [{"date": d, "saved": round(v, 2)} for d, v in sorted(daily.items())]
    return r


BUCKETS = (
    ("Under 15 min", 0, 15),
    ("15 min to 1 h", 15, 60),
    ("1 to 3 h", 60, 180),
    ("3 to 12 h", 180, 720),
    ("12 to 24 h", 720, 1440),
    ("Over 24 h", 1440, math.inf),
)


def energy_by_length(jobs: list[TraceJob], kw_per_gpu: float) -> list[dict]:
    """Share of jobs vs share of electricity by job length. On real GPU clusters a handful of very long jobs use
    most of the power, which is what limits how much timing can save."""
    counts, kwh = [0] * len(BUCKETS), [0.0] * len(BUCKETS)
    for j in jobs:
        for i, (_, lo, hi) in enumerate(BUCKETS):
            if lo <= j.duration_min < hi:
                counts[i] += 1
                kwh[i] += j.gpus * kw_per_gpu * j.duration_min / 60
                break
    n, total = len(jobs) or 1, sum(kwh) or 1
    return [{"label": b[0], "jobs_share": counts[i] / n, "energy_share": kwh[i] / total} for i, b in enumerate(BUCKETS)]


def retime_weeks(jobs: list[TraceJob], window_start: datetime) -> list[TraceJob]:
    """Shift a file from another period by whole weeks so it lands on the price window, keeping weekdays and times of
    day (the weekly rhythm) but not the real dates."""
    first = min(j.submit for j in jobs).astimezone(IST).date()
    week0 = first - timedelta(days=first.weekday())
    target = window_start.astimezone(IST).date()
    target -= timedelta(days=target.weekday())
    shift = timedelta(weeks=round((target - week0).days / 7))
    return [replace(j, submit=j.submit + shift) for j in jobs]


def sensitivity(
    jobs: list[TraceJob],
    prices: dict[datetime, float],
    rules: list[TodRule],
    a: Assumptions,
    shares: tuple[float, ...] = (0.1, 0.25, 0.5, 1.0),
    slacks: tuple[float, ...] = (6, 12, 24),
) -> list[dict]:
    """The answer depends on things a job file cannot say, so show it across the plausible range, not as one number."""
    out = []
    for share in shares:
        for slack in slacks:
            r = run_backtest(jobs, prices, rules, Assumptions(**{**a.__dict__, "flexible_share": share, "slack_hours": slack}))
            out.append({"flexible_share": share, "slack_hours": slack, "pct_saved": r.pct_saved, "saved_rs": round(r.saved_rs, 2)})
    return out


MAX_JOB_MINUTES = 366 * 24 * 60  # a year: longer is a typo, and would make the sums absurd
MAX_JOB_GPUS = 100_000


def read_jobs_csv(text: str, max_rows: int | None = None) -> list[TraceJob]:
    """Columns: job_id, submit_time, duration_minutes, gpus. A time without a zone is read as IST."""
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in ("job_id", "submit_time", "duration_minutes", "gpus") if c not in (reader.fieldnames or [])]
    if missing:
        raise BacktestInputError([f"missing column(s): {', '.join(missing)}"])
    jobs: list[TraceJob] = []
    errors: list[str] = []
    for i, row in enumerate(reader):
        line = i + 2
        if max_rows is not None and i >= max_rows:
            raise BacktestInputError([f"too many rows: the limit is {max_rows} jobs per upload"])
        try:
            submit = datetime.fromisoformat((row["submit_time"] or "").strip())  # a short row leaves the field None
        except ValueError:
            errors.append(f"line {line}: submit_time is not a date and time")
            continue
        try:
            dur = float(row["duration_minutes"])
            gpus = float(row["gpus"])
        except (ValueError, TypeError):
            errors.append(f"line {line}: duration_minutes and gpus must be numbers")
            continue
        if not math.isfinite(dur) or not 0 < dur <= MAX_JOB_MINUTES:
            errors.append(f"line {line}: duration_minutes must be above 0 and at most {MAX_JOB_MINUTES:,}")
        elif not math.isfinite(gpus) or gpus < 1 or gpus != int(gpus) or gpus > MAX_JOB_GPUS:
            errors.append(f"line {line}: gpus must be a whole number from 1 to {MAX_JOB_GPUS:,}")
        elif not (row["job_id"] or "").strip():
            errors.append(f"line {line}: job_id is empty")
        else:
            jobs.append(TraceJob(row["job_id"].strip(), submit if submit.tzinfo else submit.replace(tzinfo=IST), dur, int(gpus)))
    if errors:
        shown = errors[:20] + ([f"…and {len(errors) - 20} more"] if len(errors) > 20 else [])
        raise BacktestInputError(shown)
    if not jobs:
        raise BacktestInputError(["the file has no jobs"])
    return jobs
