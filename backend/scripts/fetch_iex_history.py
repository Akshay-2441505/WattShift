"""Cache real IEX day-ahead prices for a date range as a CSV (one polite request per day).
Usage: python -m scripts.fetch_iex_history 2026-08-17 2026-09-13 [--out data/iex_2026-08-17_2026-09-13.csv]"""
import argparse
import csv
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from app.ingest_iex import fetch_dam_date


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("start", type=date.fromisoformat)
    ap.add_argument("end", type=date.fromisoformat, help="inclusive")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between requests")
    a = ap.parse_args()
    out = a.out or Path(__file__).resolve().parents[1] / "data" / f"iex_{a.start}_{a.end}.csv"

    rows, day = [], a.start
    while day <= a.end:
        got = fetch_dam_date(day)
        rows.extend(got)
        print(f"{day}: {len(got)} blocks, min Rs{min(p for _, p in got):,.0f}/MWh", flush=True)
        day += timedelta(days=1)
        time.sleep(a.pause)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["block_start_ist", "mcp_rs_per_mwh"])
        for ts, p in rows:
            w.writerow([ts.isoformat(), f"{p:.2f}"])
    print(f"wrote {len(rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
