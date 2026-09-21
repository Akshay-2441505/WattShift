"""Feed one site a scripted sequence of agent reports (no Slurm needed): to look at the Live page, and as a demo fallback.
The site must be in autonomous mode.  Usage:
  python -m scripts.simulate_agent --url http://localhost:8000 --key wsk_... [--pace 4]
Steps: three flex jobs appear; the cloud decides start times; the agent confirms them; one is changed by its owner; two start;
two finish (the cloud then measures their saving from the reported real times)."""
import argparse
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone

FMT = "%Y-%m-%dT%H:%M:%SZ"


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime(FMT)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--key", required=True)
    p.add_argument("--pace", type=float, default=4.0, help="seconds between steps")
    a = p.parse_args()

    def sync(jobs, applied=()):
        body = {"agent_version": "0.1.0", "mode": "autonomous", "sent_at": iso(datetime.now(timezone.utc)), "jobs": jobs, "applied": list(applied)}
        req = urllib.request.Request(a.url + "/agent/v1/sync", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Site-Key": a.key})
        return json.loads(urllib.request.urlopen(req, timeout=20).read())

    now = datetime.now(timezone.utc)
    base = {"state": "PENDING", "submit_time": iso(now), "time_limit_min": 60, "max_wait_min": 1440}
    jobs = {"9101": {**base, "ref": "9101", "gpus": 8}, "9102": {**base, "ref": "9102", "gpus": 4}, "9103": {**base, "ref": "9103", "gpus": 2}}
    print("1. three flex jobs appear")
    out = sync(list(jobs.values()))
    starts = {d["ref"]: datetime.strptime(d["start_at"], FMT).replace(tzinfo=timezone.utc) for d in out["decisions"]}
    print("   the cloud decided:", {r: iso(t) for r, t in starts.items()} or "nothing (no cheaper window)")
    time.sleep(a.pace)
    print("2. the agent confirms the start times")
    sync(list(jobs.values()), [{"ref": r, "start_at": iso(t), "ok": True} for r, t in starts.items()])
    time.sleep(a.pace)
    print("3. the owner of job 9103 changes its start time")
    jobs["9103"]["override"] = True
    sync(list(jobs.values()))
    time.sleep(a.pace)
    print("4. jobs 9101 and 9102 start at their set times")
    for r in ("9101", "9102"):
        if r in starts:
            jobs[r].update(state="RUNNING", start_time=iso(starts[r]))
    sync(list(jobs.values()))
    time.sleep(a.pace)
    print("5. they finish; the cloud measures the saving")
    for r in ("9101", "9102"):
        if r in starts:
            jobs[r].update(state="COMPLETED", end_time=iso(starts[r] + timedelta(minutes=60)))
    sync(list(jobs.values()))
    print("done: open the Live cluster page")


if __name__ == "__main__":
    main()
