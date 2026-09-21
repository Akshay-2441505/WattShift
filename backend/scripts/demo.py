"""Scripted proof of the three v1 success criteria, on real IEX prices, in minutes.

Start the server first (one worker; DEMO_MODE=1 exposes the replay endpoints):
    $env:DEMO_MODE="1"; $env:SCHEDULER="1"; $env:DISPATCH_SECONDS="2"
    python -m uvicorn app.main:app --port 8000
Then:  python -m scripts.demo [--jobs 4] [--scale 180] [--start 19:00]

Replay = a fast simulated clock + a real historical IEX day (2026-09-18) mapped onto simulated today/tomorrow.
Jobs are REALLY fired on Kaggle GPUs; only the clock and the price day are replayed.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

IST = timezone(timedelta(hours=5, minutes=30))


def hm(iso: str | None) -> str:
    return datetime.fromisoformat(iso).astimezone(IST).strftime("%a %H:%M") if iso else "-"


def cheapest_run(blocks: list[dict], deadline: datetime, minutes: int) -> tuple[datetime, float] | None:
    """Uncapped best start for a job (ignores jitter): the contiguous run with the lowest mean effective rate."""
    k = -(-minutes // 15)
    best = None
    for i in range(len(blocks) - k + 1):
        run = blocks[i : i + k]
        start = datetime.fromisoformat(run[0]["ts"])
        if start + timedelta(minutes=minutes) > deadline:
            break
        if any(datetime.fromisoformat(run[j + 1]["ts"]) - datetime.fromisoformat(run[j]["ts"]) != timedelta(minutes=15) for j in range(k - 1)):
            continue
        rate = sum(b["effective_rate"] for b in run) / k
        if best is None or rate < best[1]:
            best = (start, rate)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--duration", type=int, default=60, help="modeled job minutes (the real Kaggle burst is short)")
    ap.add_argument("--scale", type=float, default=180)
    ap.add_argument("--start", default="19:00")
    ap.add_argument("--timeout", type=int, default=1500, help="real seconds to wait for all jobs")
    ap.add_argument("--api-key", default=os.environ.get("WATTSHIFT_API_KEY"))
    a = ap.parse_args()
    h = {"X-API-Key": a.api_key} if a.api_key else {}
    c = httpx.Client(base_url=a.url, headers=h, timeout=60)

    r = c.post("/demo/replay", json={"start": a.start, "scale": a.scale, "reset": True})
    if r.status_code == 404:
        print("Demo endpoints are off. Restart the server with DEMO_MODE=1 (see the docstring).")
        return 2
    r.raise_for_status()
    sim_start = datetime.fromisoformat(r.json()["sim_start"])
    print(f"REPLAY MODE: simulated clock x{a.scale:g} starting {sim_start.astimezone(IST):%a %H:%M} IST; "
          f"real IEX day (2026-09-18) replayed. Jobs run on REAL Kaggle GPUs.\n")

    fc = c.get("/forecast").json()
    blocks = fc["blocks"]
    now = datetime.fromisoformat(fc["now"])
    # Tomorrow 16:30 IST. Real Kaggle runs take ~1 min real time, which the fast clock stretches to hours of simulated
    # time, so waiting jobs need slack before the deadline (still inside the solar zone, which ends 17:00).
    deadline = (sim_start + timedelta(days=1)).replace(hour=16, minute=30)
    best = cheapest_run(blocks, deadline, a.duration)
    if not best:
        print("No priced window before the deadline; nothing to schedule.")
        return 1
    print(f"Now {hm(fc['now'])} ({blocks[0]['tod_zone']} tariff x{blocks[0]['tod_multiplier']:.2f}); "
          f"deadline {deadline:%a %H:%M}. Cheapest {a.duration}-min window if uncapped: ~{best[0].astimezone(IST):%a %H:%M}.\n")

    jobs = []
    for i in range(a.jobs):
        r = c.post("/jobs", json={"duration_minutes": a.duration, "deadline": deadline.isoformat(), "provider": "kaggle"})
        j = r.json() if r.status_code == 201 else r.json().get("job", {})
        jobs.append(j)
        held = "HELD" if j.get("assigned_window_start") else "QUEUED (no capacity)"
        print(f"job {i+1}: {held} -> window {hm(j.get('assigned_window_start'))} [{j.get('tod_zone')}]  "
              f"baseline Rs{j.get('baseline_cost_rs')} -> planned Rs{j.get('planned_cost_rs')}")

    starts = {j["assigned_window_start"] for j in jobs if j.get("assigned_window_start")}
    displaced = sum(1 for j in jobs if j.get("assigned_window_start") and
                    abs((datetime.fromisoformat(j["assigned_window_start"]) - best[0]).total_seconds()) > 1800)  # > jitter (15 min) + slack
    print(f"\n{len(starts)} distinct start times; {displaced} job(s) pushed off the cheapest window by the capacity cap.\n")

    ids = [j["id"] for j in jobs]
    seen: dict[str, str] = {}
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        cur = {i: c.get(f"/jobs/{i}").json() for i in ids}
        sim_now = c.get("/forecast?hours=1").json()["now"]
        for i, j in cur.items():
            if seen.get(i) != j["status"]:
                seen[i] = j["status"]
                print(f"[sim {hm(sim_now)} | real {time.time()-t0:5.0f}s] job {ids.index(i)+1} -> {j['status']}"
                      + (f" ({j['failure_reason']})" if j.get("failure_reason") else ""))
        if all(j["status"] in ("done", "failed") for j in cur.values()):
            break
        time.sleep(3)
    else:
        print("Timed out waiting for jobs.")

    print("\nRESULT")
    for n, i in enumerate(ids, 1):
        j = c.get(f"/jobs/{i}").json()
        print(f"job {n}: {j['status']:6}  planned {hm(j['assigned_window_start'])}  started {hm(j['executed_at'])}  "
              f"job {j['id'][:8]}")
    s = c.get("/savings/summary").json()
    print(f"\nSAVED Rs{s['total']:.2f} across {s['jobs_counted']} job(s) "
          f"({s['pct_saved']}% vs running immediately; ToD tariff x modeled 10 kW node)")
    final = [c.get(f"/jobs/{i}").json() for i in ids]
    ran = [j for j in final if j["status"] == "done" and j["executed_at"]]
    held_then_ran = [j for j in ran if datetime.fromisoformat(j["executed_at"]) - datetime.fromisoformat(j["submitted_at"]) >= timedelta(hours=1)]
    failed = [j for j in final if j["status"] == "failed"]
    ok = s["jobs_counted"] > 0 and s["total"] > 0
    print(f"\n{len(ran)} of {len(final)} jobs ran on Kaggle; {len(failed)} failed" + "".join(f"\n  - job {ids.index(j['id'])+1}: {j['failure_reason']}" for j in failed))
    print(f"criterion 1 (held, fired hours later, no manual step): {'PASS' if held_then_ran else 'FAIL'} ({len(held_then_ran)}/{len(final)} jobs)")
    print("criterion 2 (non-zero Rs saved from real ToD difference):", "PASS" if ok else "FAIL")
    print("criterion 3 (>=2 different windows because of the cap):", "PASS" if len(starts) >= 2 and displaced >= 1 else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
