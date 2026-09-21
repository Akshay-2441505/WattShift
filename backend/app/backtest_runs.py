"""Runs 'what would you have saved?' reports in the background: one takes 10-60 s, far too long for a web request.
In-memory and single-process by design (reports are cheap to re-run); the newest few are kept."""
import csv
import logging
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from app.backtest import (
    Assumptions,
    BacktestInputError,
    TraceJob,
    energy_by_length,
    read_jobs_csv,
    retime_weeks,
    run_backtest,
    sensitivity,
)
from app.config import settings
from app.seed import TOD_SEED

log = logging.getLogger("wattshift.backtest")

DATA = Path(__file__).resolve().parents[1] / "data"
SAMPLE_JOBS = DATA / "helios_venus_sample.csv"
PRICES_CSV = DATA / "iex_2026-08-17_2026-09-13.csv"
SAMPLE_FLEET_GPUS = 968  # the Venus cluster in June 2020, from the trace's own cluster_gpu_number.csv
TARIFF = "MSEDCL HT-I(A) Industry, FY 2026-27 (MERC Case No. 75 of 2025)"

MAX_UPLOAD_ROWS = 20_000
MAX_ACTIVE = 3  # reports queued or running at once; more get a 429
MAX_KEEP = 20
SENSITIVITY_SHARES = (0.25, 0.5, 1.0)
SENSITIVITY_SLACKS = (6, 12, 24)

_pool = ThreadPoolExecutor(max_workers=1)  # the work is CPU-bound; one at a time keeps the API responsive
_runs: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


@lru_cache(maxsize=1)
def prices() -> dict[datetime, float]:
    with open(PRICES_CSV, newline="") as f:
        return {datetime.fromisoformat(r["block_start_ist"]): float(r["mcp_rs_per_mwh"]) for r in csv.DictReader(f)}


def price_window() -> tuple[datetime, datetime]:
    p = prices()
    return min(p), max(p) + timedelta(minutes=15)


def sample_info() -> dict:
    lo, hi = price_window()
    with open(SAMPLE_JOBS, newline="") as f:
        n = sum(1 for _ in f) - 1
    return {
        "jobs": n,
        "fleet_gpus": SAMPLE_FLEET_GPUS,
        "price_window": {"from": lo.date().isoformat(), "to": (hi - timedelta(minutes=15)).date().isoformat()},
        "source": (
            "Helios (SenseTime, CC-BY-4.0): a real job log from a 2020 GPU research cluster, moved onto "
            f"{lo:%d %b} to {(hi - timedelta(minutes=15)):%d %b %Y} keeping weekdays and times of day. "
            "It is not an Indian GPU provider's data."
        ),
    }


def prepare(source: str, csv_text: str | None, retime: bool) -> list[TraceJob]:
    """Turn the request into jobs, raising BacktestInputError with plain-language problems."""
    if source == "sample":
        jobs = read_jobs_csv(SAMPLE_JOBS.read_text(encoding="utf-8"))
    elif not csv_text or not csv_text.strip():
        raise BacktestInputError(["Choose a jobs file to upload."])
    else:
        jobs = read_jobs_csv(csv_text, max_rows=MAX_UPLOAD_ROWS)
    lo, hi = price_window()
    if retime:
        jobs = retime_weeks(jobs, lo)
    outside = sum(1 for j in jobs if not lo <= j.submit < hi)
    if outside:
        raise BacktestInputError([
            f"{outside:,} of {len(jobs):,} jobs were submitted outside the price data we have "
            f"({lo:%d %b %Y} to {(hi - timedelta(minutes=15)):%d %b %Y}). Turn on 'line my dates up with the price "
            "window', or fetch prices for your period with scripts/fetch_iex_history."
        ])
    return jobs


def _report(jobs: list[TraceJob], r, a: Assumptions, retimed: bool) -> dict:
    return {
        "assumptions": {k: v for k, v in a.__dict__.items() if k != "base_rate_rs_kwh"},
        "period": {"from": min(j.submit for j in jobs).isoformat(), "to": max(j.submit for j in jobs).isoformat()},
        "tariff": TARIFF,
        "retimed": retimed,
        "totals": {
            "jobs": r.n_jobs, "kwh": r.kwh_total, "baseline_rs": r.baseline_rs, "scheduled_rs": r.scheduled_rs,
            "saved_rs": r.saved_rs, "pct_saved": r.pct_saved, "n_eligible": r.n_eligible, "n_flexible": r.n_flexible,
            "n_placed": r.n_placed, "n_unplaced": r.n_unplaced, "avg_delay_hours": r.avg_delay_hours,
            "flexible_energy_share": r.flexible_energy_share, "max_block_gpus": r.max_block_gpus,
        },
        "energy_by_length": energy_by_length(jobs, a.kw_per_gpu),
        "kwh_by_zone_before": r.kwh_by_zone_before,
        "kwh_by_zone_after": r.kwh_by_zone_after,
        "hourly_kwh_before": r.hourly_kwh_before,
        "hourly_kwh_after": r.hourly_kwh_after,
        "daily": r.daily,
        "sensitivity": None,  # filled in once the slower grid finishes
    }


def _work(run: dict, jobs: list[TraceJob], a: Assumptions, retimed: bool) -> None:
    try:
        run["stage"] = "main"
        r = run_backtest(jobs, prices(), TOD_SEED, a)
        res = _report(jobs, r, a, retimed)
        run["result"] = res  # the headline is readable while the grid is still being worked out
        run["stage"] = "scenarios"
        res["sensitivity"] = sensitivity(jobs, prices(), TOD_SEED, a, SENSITIVITY_SHARES, SENSITIVITY_SLACKS)
        run["stage"] = "finished"
        run["status"] = "done"
    except Exception as e:  # never let a bad run kill the worker thread
        log.exception("backtest failed")
        run["status"], run["error"] = "error", f"The report could not be produced: {type(e).__name__}: {e}"


def active_count() -> int:
    with _lock:
        return sum(1 for r in _runs.values() if r["status"] in ("queued", "running"))


def start(jobs: list[TraceJob], a: Assumptions, retimed: bool = False) -> str | None:
    """Queue a report. Returns its id, or None if too many are already waiting."""
    with _lock:
        if sum(1 for r in _runs.values() if r["status"] in ("queued", "running")) >= MAX_ACTIVE:
            return None
        run_id = uuid.uuid4().hex[:12]
        run = {"id": run_id, "status": "queued", "stage": "queued", "error": None, "result": None}
        _runs[run_id] = run
        while len(_runs) > MAX_KEEP:
            _runs.popitem(last=False)
    run["status"] = "running"
    _pool.submit(_work, run, jobs, a, retimed)
    return run_id


def get(run_id: str) -> dict | None:
    with _lock:
        run = _runs.get(run_id)
    return dict(run) if run else None


def default_assumptions(**kw) -> Assumptions:
    return Assumptions(base_rate_rs_kwh=settings.base_rate_rs_kwh, **kw)
