"""Turn a slice of the public Helios trace (SenseTime, CC-BY-4.0) into our import format, re-timed onto a real IEX window.

Helios is from 2020 and IEX's 15-minute data starts in 2022, so a slice is shifted by a whole number of WEEKS onto a
recent window: weekdays and times of day are preserved, dates are not. Timestamps are read as local (IST) clock time.
Usage:
  python -m scripts.convert_helios <helios>/data/Venus 2020-06-01 2026-08-17 --days 28 --out data/helios_venus_sample.csv
Both start dates must fall on the same weekday. Only jobs that used at least one GPU and ran for more than 0 s are kept.
"""
import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cluster_dir", type=Path, help="a Helios cluster folder holding cluster_log.csv and cluster_gpu_number.csv")
    ap.add_argument("slice_start", type=date.fromisoformat, help="first day of the slice in the 2020 trace")
    ap.add_argument("target_start", type=date.fromisoformat, help="the day it is re-timed onto")
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.slice_start.weekday() != a.target_start.weekday():
        sys.exit("slice_start and target_start must be the same weekday, so the weekly rhythm is preserved")

    shift = datetime.combine(a.target_start, datetime.min.time()) - datetime.combine(a.slice_start, datetime.min.time())
    lo = datetime.combine(a.slice_start, datetime.min.time())
    hi = lo + timedelta(days=a.days)
    tag = a.cluster_dir.name.lower()

    kept = skipped = 0
    with open(a.cluster_dir / "cluster_log.csv", newline="") as src, open(a.out, "w", newline="") as dst:
        w = csv.writer(dst)
        w.writerow(["job_id", "submit_time", "duration_minutes", "gpus"])
        for row in csv.DictReader(src):
            submit = datetime.fromisoformat(row["submit_time"])
            if not lo <= submit < hi:
                continue
            gpus, dur = int(row["gpu_num"]), int(row["duration"] or 0)
            if gpus < 1 or dur <= 0:
                skipped += 1
                continue
            w.writerow([f"{tag}-{row['job_id']}", (submit + shift).isoformat(sep=" "), round(dur / 60, 2), gpus])
            kept += 1

    fleet = []
    with open(a.cluster_dir / "cluster_gpu_number.csv", newline="") as f:
        for row in csv.DictReader(f):
            if lo.date().isoformat() <= row["date"] < hi.date().isoformat():
                fleet.append(float(row["total"]))
    print(f"kept {kept:,} GPU jobs ({skipped:,} zero-length or CPU-only skipped) -> {a.out}")
    print(f"fleet size in the slice: about {round(sum(fleet) / len(fleet)) if fleet else '?'} GPUs (mean of the daily totals)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
