"""Replay past jobs against real IEX prices and the official tariff, and print what timing would have saved.
Usage:
  python -m scripts.backtest --jobs data/helios_venus_sample.csv --prices data/iex_2026-08-17_2026-09-13.csv \\
      [--flexible 0.5] [--slack 12] [--gpus 968] [--kw-per-gpu 1.25] [--capacity 0.5]
The jobs file needs: job_id, submit_time, duration_minutes, gpus (times without a zone are read as IST)."""
import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

from app.backtest import Assumptions, read_jobs_csv, run_backtest, sensitivity
from app.config import settings
from app.seed import TOD_SEED


def read_prices(path: Path) -> dict[datetime, float]:
    with open(path, newline="") as f:
        return {datetime.fromisoformat(r["block_start_ist"]): float(r["mcp_rs_per_mwh"]) for r in csv.DictReader(f)}


def inr(n: float) -> str:
    return f"Rs {n:,.0f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=Path, required=True)
    ap.add_argument("--prices", type=Path, required=True)
    ap.add_argument("--flexible", type=float, default=0.5, help="share of shiftable jobs that may wait")
    ap.add_argument("--slack", type=float, default=12, help="hours a flexible job may wait")
    ap.add_argument("--gpus", type=int, default=968, help="fleet size (Helios Venus, June 2020)")
    ap.add_argument("--kw-per-gpu", type=float, default=1.25)
    ap.add_argument("--capacity", type=float, default=0.5, help="share of the fleet that may run shifted work per 15 min")
    a = ap.parse_args()

    jobs = read_jobs_csv(a.jobs.read_text(encoding="utf-8"))
    prices = read_prices(a.prices)
    asm = Assumptions(a.flexible, a.slack, a.kw_per_gpu, a.gpus, a.capacity, 180, settings.base_rate_rs_kwh)
    r = run_backtest(jobs, prices, TOD_SEED, asm)

    first, last = min(j.submit for j in jobs), max(j.submit for j in jobs)
    print(f"{r.n_jobs:,} jobs submitted {first:%d %b %Y} to {last:%d %b %Y}; {r.kwh_total:,.0f} kWh; tariff: MSEDCL HT-I FY 2026-27")
    print(f"  short enough to shift (<= 3 h): {r.n_eligible:,} jobs ({r.n_eligible / r.n_jobs:.0%}), "
          f"but only {r.flexible_energy_share:.0%} of the energy is in jobs assumed able to wait")
    print(f"  assumed able to wait {a.slack:g} h: {r.n_flexible:,} jobs; placed {r.n_placed:,}, no room {r.n_unplaced:,}; "
          f"average wait {r.avg_delay_hours:.1f} h")
    print(f"\nBill if every job ran at once : {inr(r.baseline_rs)}")
    print(f"Bill with Wattshift scheduling: {inr(r.scheduled_rs)}")
    print(f"SAVED                         : {inr(r.saved_rs)}  ({r.pct_saved:.1f}%)")

    def zone_pct(d):
        tot = sum(d.values()) or 1
        return "  ".join(f"{z} {v / tot:.0%}" for z, v in d.items())

    print(f"\nEnergy by tariff zone  before: {zone_pct(r.kwh_by_zone_before)}")
    print(f"                       after : {zone_pct(r.kwh_by_zone_after)}")
    print(f"Most shifted GPUs sharing one 15-min block: {r.max_block_gpus:,.0f} (fleet {a.gpus:,})")

    print("\nHow the answer changes with the assumptions (% of the bill saved):")
    grid = sensitivity(jobs, prices, TOD_SEED, asm)
    slacks = sorted({g["slack_hours"] for g in grid})
    print("  jobs able to wait  " + "".join(f"{s:>9g} h" for s in slacks))
    for share in sorted({g["flexible_share"] for g in grid}):
        row = {g["slack_hours"]: g["pct_saved"] for g in grid if g["flexible_share"] == share}
        print(f"  {share:>15.0%}    " + "".join(f"{row[s]:>10.1f}%" for s in slacks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
