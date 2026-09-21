"""The end-to-end proof: the real agent, a real Slurm (Docker) and the real cloud code (isolated test database).
Two modes. fast (default): about 17 minutes at any time of day; it does not wait for the cheap window, and the "Slurm
starts a job on time with the agent and the cloud stopped" test uses a small stand-in cloud that hands out a start time 2.5
minutes away. full: idles until 13 minutes before a top-of-hour, then carries the REAL cloud's own decision all the way to a
real start with the agent and the cloud stopped."""
import argparse
import sys
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "backend")]
import agentproc  # noqa: E402
import cloud  # noqa: E402
import lab  # noqa: E402
import stubcloud  # noqa: E402

OUT = HERE.parent / "docs" / "superpowers" / "e2e"
RAW = OUT / "raw"
LAG = 120  # a deferred job may start this long after its set time and still count as "on time" (Slurm's scheduler cycle)
WINDOW = 60  # the proof's cloud has the start-time spread off, so a job's planned start is the boundary itself (plus slack)
MIN_LEAD = timedelta(minutes=13)  # time the tariff-dependent phases need before the cheap window opens
BLOCK_S = 80  # blockers hold every GPU this long; the agent looks every 10-30 s, so this is plenty
STUB_DELAY_S = 150  # the stand-in cloud hands out "now + 150 s"
OWNER_DELAY_S = 150  # the owner moves her own job to "now + 150 s"


class Report:
    def __init__(self):
        self.rows: list[tuple] = []
        self.gaps: dict[str, int] = {}
        self.notes: list[str] = []

    def check(self, criterion, name, ok, detail=""):
        self.rows.append((str(criterion), name, bool(ok), str(detail)))
        print(f"{'PASS' if ok else 'FAIL'}  [{criterion}] {name}  {detail}", flush=True)

    def save(self, name, text):
        RAW.mkdir(parents=True, exist_ok=True)
        (RAW / f"{name}.txt").write_text(text, encoding="utf-8")

    def markdown(self, ctx) -> str:
        failed = sum(1 for r in self.rows if not r[2])
        lines = [
            f"# End-to-end proof on a real Slurm ({datetime.now():%Y-%m-%d}, {ctx.mode} mode)", "",
            f"**Result: {len(self.rows) - failed} of {len(self.rows)} checks passed.**", "",
            "Real: the agent (unmodified), Slurm 26.05.2 in Docker (two nodes, four fake GPUs each, strict PrivateData, the agent as an "
            "Operator), the cloud's own code with a real database (the test database). Synthetic: the tariff (peak until a top-of-hour, then "
            f"solar; boundary {datetime.fromtimestamp(ctx.boundary, cloud.IST):%H:%M} IST), constant electricity prices, and the per-job "
            "start-time spread, which is switched off in the proof's cloud (it is unit-tested elsewhere). "
            + ("Checks named 'stand-in cloud' used a small stand-in for the cloud because the real cloud cannot hand out a start time a few "
               "minutes away; every other check used the real cloud. " if ctx.mode == "fast" else "")
            + "Not covered here: the dashboard, typed GPUs, real GPU hardware.", "",
            "| Criterion | Check | Result | Detail |", "|---|---|---|---|",
        ]
        lines += [f"| {c} | {n} | {'PASS' if ok else '**FAIL**'} | {d} |" for c, n, ok, d in self.rows]
        if self.gaps:
            lines += ["", "## Start accuracy with the agent and the cloud stopped", "",
                      "Seconds between the start time that was set and the real start (never negative: Slurm never started a job early):", ""]
            lines += [f"- {k}: +{v} s" for k, v in self.gaps.items()]
        if self.notes:
            lines += ["", "## Notes", ""] + [f"- {n}" for n in self.notes]
        lines += ["", f"Raw outputs: `docs/superpowers/e2e/raw/` ({', '.join(sorted(p.name for p in RAW.glob('*.txt')))})", ""]
        return "\n".join(lines)


@dataclass
class Ctx:
    report: Report
    mode: str
    boundary: int
    site_id: str
    key: str
    cloud: object = None
    agent: object = None
    stub: object = None
    jobs: dict = field(default_factory=dict)
    applied: dict = field(default_factory=dict)  # job name -> the start time (epoch) the real cloud decided, as seen in Slurm
    finals: dict = field(default_factory=dict)  # jobs the reconcile phase checks, name -> job id
    t_user: int = 0


def remaining(ctx, extra=0) -> int:
    return max(30, int(ctx.boundary + extra - time.time()))


def require_lead(ctx, minutes):
    left = (ctx.boundary - time.time()) / 60
    if left < minutes:
        raise RuntimeError(f"only {left:.1f} minutes before the cheap window opens; this phase needs {minutes}. Re-run the proof.")


def reachable(url) -> bool:
    try:
        urllib.request.urlopen(url + "/health", timeout=2)
        return True
    except OSError:
        return False


def start_blockers() -> list[int]:
    """Two 4-GPU jobs by bob (qos normal, so never touched) that keep every GPU busy for about 80 seconds."""
    ids = [lab.sbatch("bob", f"e2e-block{i}", qos="normal", gres=4, minutes=2, sleep_s=BLOCK_S) for i in (1, 2)]
    lab.until(lambda: all(lab.state_of(i) == "RUNNING" for i in ids), 90, what="the blockers to run")
    return ids


def deferred_eligible(job, boundary):
    e = lab.epoch(lab.show(job).get("EligibleTime"))
    return e if e and e >= boundary else None


# --- phases -------------------------------------------------------------------------------------------------------------
def phase_preflight(ctx):
    r = ctx.report
    nodes = lab.wait_nodes_idle(60)
    r.check("F", "the lab has two idle nodes with 4 GPUs each", True, nodes.replace("\n", " | "))
    p = agentproc.cli("shadow", "check")
    r.save("agent-check", p.stdout + p.stderr)
    last = (p.stdout.strip().splitlines() or [""])[-1]
    r.check("F", "wattshift-agent check passes against the real Slurm as an Operator", p.returncode == 0, last or p.stderr[:200])


def phase_shadow(ctx):
    r = ctx.report
    cloud.api("POST", f"/sites/{ctx.site_id}/mode", {"mode": "shadow"})
    blockers = start_blockers()
    s1 = lab.sbatch("alice", "e2e-S1", gres=2, minutes=3, sleep_s=20)
    ctx.agent = agentproc.start("shadow")
    seen = lab.until(lambda: cloud.decisions(str(s1)), 120, what="the cloud to record a decision for S1")
    info = lab.show(s1)
    r.save("shadow-scontrol-S1", "\n".join(f"{k}={v}" for k, v in info.items()))
    r.check(1, "shadow: no start time was set on the job", abs(int(info["EligibleTime"]) - int(info["SubmitTime"])) <= 2,
            f"eligible-submit = {int(info['EligibleTime']) - int(info['SubmitTime'])} s")
    writes = [c for c in agentproc.audit_commands() if c[:2] == ["scontrol", "update"]]
    r.check(1, "shadow: the agent ran no write command (its own audit log)", not writes, f"{len(agentproc.audit_commands())} commands, {len(writes)} writes")
    d = seen[0]
    r.check(1, "shadow: the cloud recorded what it WOULD have done", d["mode"] == "shadow" and d["planned_cost"] < d["baseline_cost"],
            f"planned {float(d['planned_cost']):.2f} < baseline {float(d['baseline_cost']):.2f} Rs")
    end = lab.wait_finished(blockers, 300)
    start = lab.until(lambda: (lab.acct(s1) or {}).get("start"), 300, what="S1 to start")
    r.check(1, "shadow: the job started as soon as the GPUs freed up (not deferred)", start - end <= 90, f"{start - end} s after the blockers ended")
    agentproc.stop(ctx.agent)
    ctx.agent = None


def phase_release(ctx):
    r = ctx.report
    cloud.api("POST", f"/sites/{ctx.site_id}/mode", {"mode": "autonomous"})
    require_lead(ctx, 7)
    blockers = start_blockers()
    r1 = lab.sbatch("alice", "e2e-R1", gres=2, minutes=2, sleep_s=20)
    r2 = lab.sbatch("alice", "e2e-R2", gres=1, minutes=2, sleep_s=20)
    ctx.agent = agentproc.start("autonomous")
    e = [lab.until(lambda j=j: deferred_eligible(j, ctx.boundary), 150, what=f"job {j} to be deferred") for j in (r1, r2)]
    r.check(2, "autonomous: flex jobs are deferred into the cheap window", all(ctx.boundary <= x <= ctx.boundary + WINDOW for x in e),
            f"eligible {[x - ctx.boundary for x in e]} s after the boundary")
    p = agentproc.cli("autonomous", "release-all")
    r.save("release-all-cli", p.stdout + p.stderr)
    lab.until(lambda: all((lab.epoch(lab.show(j).get("EligibleTime")) or ctx.boundary) < ctx.boundary for j in (r1, r2)), 30, what="both jobs to be released")
    r.check(4, "release-all sets every deferred job back to 'start now' (works without the cloud's help)", p.returncode == 0, p.stdout.strip())
    lab.until(lambda: cloud.site_row()["release_all"], 90, what="the cloud's kill switch to turn on")
    r.check(4, "the cloud's kill switch turned on at the next sync", True)
    end = lab.wait_finished(blockers, 300)
    starts = [lab.until(lambda j=j: (lab.acct(j) or {}).get("start"), 240, what=f"job {j} to start") for j in (r1, r2)]
    r.check(4, "released jobs started as soon as the GPUs freed up", all(s - end <= 90 and s < ctx.boundary for s in starts),
            f"{[s - end for s in starts]} s after the blockers ended")
    cloud.api("POST", f"/sites/{ctx.site_id}/release-all", {"on": False})
    lab.until(lambda: "release requested: False" in agentproc.cli("autonomous", "status").stdout, 90, what="the agent to clear its release request")
    m = cloud.managed(str(r1))
    r.check(4, "a released job is not managed again", m and m["plan_status"] == "released", m and m["plan_status"])
    agentproc.stop(ctx.agent)
    ctx.agent = None


def phase_main(ctx):
    r = ctx.report
    require_lead(ctx, 3.5)
    blockers = start_blockers()
    a = {
        "A1": lab.sbatch("alice", "e2e-A1", gres=2, minutes=2, sleep_s=20),
        "A3": lab.sbatch("alice", "e2e-A3", gres=1, minutes=2, sleep_s=20),
        "A4": lab.sbatch("alice", "e2e-A4", gres=1, minutes=2, sleep_s=20),
        "A5": lab.sbatch("alice", "e2e-A5", gpus=2, minutes=2, sleep_s=20),
    }
    a6 = lab.sbatch("alice", "e2e-A6-array", gres=1, minutes=2, sleep_s=20, extra=["--array=1-2"])
    a7 = lab.sbatch("alice", "e2e-A7-dependent", gres=1, minutes=2, sleep_s=20, extra=[f"--dependency=afterok:{a['A1']}"])
    n1 = lab.sbatch("bob", "e2e-N1", qos="normal", gres=1, minutes=2, sleep_s=20)
    ctx.jobs = {**a, "A6": a6, "A7": a7}
    ctx.agent = agentproc.start("autonomous")

    ctx.applied = {n: lab.until(lambda j=j: deferred_eligible(j, ctx.boundary), 150, what=f"{n} to be deferred") for n, j in a.items()}
    r.check(2, "autonomous: every flex job was deferred into the cheap window by the real cloud",
            all(ctx.boundary <= e <= ctx.boundary + WINDOW for e in ctx.applied.values()), {n: e - ctx.boundary for n, e in ctx.applied.items()})
    r.save("main-scontrol-A1", "\n".join(f"{k}={v}" for k, v in lab.show(a["A1"]).items()))
    lab.until(lambda: all((cloud.managed(str(j)) or {}).get("applied_start") for j in a.values()), 90, what="the cloud to receive the applied reports")
    same = all(int(cloud.managed(str(j))["applied_start"].timestamp()) == ctx.applied[n] for n, j in a.items())
    r.check(2, "the start time in Slurm equals the one the real cloud decided", same, "for all four jobs")
    gp = {n: cloud.managed(str(j))["gpus"] for n, j in a.items()}
    r.check(2, "GPU counts were read correctly from a real Slurm (--gres and --gpus styles)", gp == {"A1": 2, "A3": 1, "A4": 1, "A5": 2}, gp)
    m6, m7 = cloud.managed_prefix(str(a6)), cloud.managed(str(a7))
    r.check(2, "an array job and a dependent job are reported as skipped and never deferred",
            m6 and m7 and m6["plan_status"] == "skipped" and m7["plan_status"] == "skipped" and not m6["applied_start"] and not m7["applied_start"],
            f"{m6 and m6['note']}, {m7 and m7['note']}")
    r.check(2, "a non-flex job was never read or sent",
            cloud.managed(str(n1)) is None and not any(str(n1) in c for c in agentproc.audit_commands()), f"job {n1}")

    ctx.t_user = int(time.time()) + OWNER_DELAY_S
    lab.dexec(["scontrol", "update", f"JobId={a['A3']}", f"StartTime=now+{OWNER_DELAY_S}"], user="alice")  # the owner changes her own job
    lab.until(lambda: (cloud.managed(str(a["A3"])) or {}).get("plan_status") == "abandoned", 150, what="the cloud to stop managing A3")
    r.check(5, "a job its owner changed is no longer managed", True, cloud.managed(str(a["A3"]))["note"])

    agentproc.stop(ctx.agent)
    cloud.stop(ctx.cloud)
    ctx.agent = ctx.cloud = None
    end = lab.wait_finished(blockers, 300)
    n1s = lab.until(lambda: (lab.acct(n1) or {}).get("start"), 200, what="N1 to start")
    r.check(2, "a non-flex job started as soon as the GPUs freed up", n1s - end <= 90, f"{n1s - end} s after the blockers ended")
    (tail_full if ctx.mode == "full" else tail_fast)(ctx, a)


def check_owner_time(ctx, a):
    s3 = lab.until(lambda: (lab.acct(a["A3"]) or {}).get("start"), 400, what="A3 to start")
    ctx.report.check(5, "the owner's start time was respected (the agent did not fight it)",
                     ctx.t_user <= s3 <= ctx.t_user + LAG and s3 < ctx.applied["A3"],
                     f"started {s3 - ctx.t_user} s after the owner's time; the time we had set was {ctx.applied['A3'] - s3} s later")


def tail_full(ctx, a):
    """Full mode: the jobs the REAL cloud deferred are left to start at the cheap window with the agent and the cloud stopped."""
    r = ctx.report
    r.check(3, "the agent and the real cloud are both stopped", not cloud.is_up() and ctx.agent is None)
    check_owner_time(ctx, a)
    for n in ("A1", "A4", "A5"):
        s = lab.until(lambda j=a[n]: (lab.acct(j) or {}).get("start"), remaining(ctx, WINDOW + 4 * LAG), every=10, what=f"{n} to start")
        r.gaps[n] = s - ctx.applied[n]
        r.check(3, f"{n} started at the real cloud's decided time with the agent and the cloud stopped", 0 <= s - ctx.applied[n] <= LAG, f"+{s - ctx.applied[n]} s")
    lab.wait_finished([a["A1"], a["A4"], a["A5"]], 300)
    ctx.finals = {n: a[n] for n in ("A1", "A4", "A5")}


def failopen_with_stand_in(ctx):
    """Fast mode: prove Slurm holds and starts a job at the set time with the agent and the cloud stopped, using a stand-in cloud."""
    r = ctx.report
    stub = ctx.stub = stubcloud.StubCloud(ctx.key, delay_s=STUB_DELAY_S)
    stub.start()
    blockers = start_blockers()
    f = {"F1": lab.sbatch("alice", "e2e-F1", gres=1, minutes=2, sleep_s=20), "F2": lab.sbatch("alice", "e2e-F2", gpus=2, minutes=2, sleep_s=20)}
    stub.targets = {str(j) for j in f.values()}
    ctx.agent = agentproc.start("autonomous", url=stub.base, state="state-stub.db")

    def set_time(j):
        e = lab.epoch(lab.show(j).get("EligibleTime"))
        return e if e and e > time.time() + 60 else None

    set_at = {n: lab.until(lambda j=j: set_time(j), 90, what=f"{n} to be deferred by the agent") for n, j in f.items()}
    r.check(3, "(stand-in cloud) the start time in Slurm equals the one handed out", all(set_at[n] == stub.decided[str(j)] for n, j in f.items()),
            {n: set_at[n] - int(time.time()) for n in f})
    lab.until(lambda: all(stub.applied.get(str(j)) for j in f.values()), 60, what="the agent to confirm both start times")
    end = lab.wait_finished(blockers, 300)
    held = {n: lab.state_of(j) == "PENDING" and time.time() < set_at[n] for n, j in f.items()}
    r.check(3, "(stand-in cloud) Slurm held both jobs although the GPUs were free", all(held.values()), f"the blockers ended {set_at['F1'] - end} s before the set time")
    agentproc.stop(ctx.agent)
    stub.stop()
    ctx.agent = None
    r.check(3, "(stand-in cloud) the agent and the stand-in cloud are both stopped", not reachable(stub.base))
    for n, j in f.items():
        s = lab.until(lambda j=j: (lab.acct(j) or {}).get("start"), STUB_DELAY_S + 4 * LAG, every=5, what=f"{n} to start")
        r.gaps[n] = s - set_at[n]
        r.check(3, f"(stand-in cloud) {n} started at its set time with the agent and the cloud stopped", 0 <= s - set_at[n] <= LAG, f"+{s - set_at[n]} s")
    lab.wait_finished(list(f.values()), 120)
    ctx.finals = f


def tail_fast(ctx, a):
    failopen_with_stand_in(ctx)
    check_owner_time(ctx, a)
    for n in ("A1", "A4", "A5", "A6", "A7"):  # deferred by the real cloud to the cheap window; fast mode does not wait for them
        lab.dexec(["scancel", str(ctx.jobs[n])], user="alice", check=False)
    ctx.report.notes.append("Fast mode: A1, A4 and A5 (deferred by the REAL cloud to the cheap window) were cancelled while held; only full mode waits for them to start.")


def check_measured(r, n, m):
    """The cloud replaces the planning estimate with the real runtime, so compare the MEASURED figures with a hand calculation
    (the proof's tariff: peak +25% before the boundary, solar -15% after; base Rs 8.44/kVAh; 1.25 kW per GPU; whole minutes, at least 1)."""
    minutes = max(1, round((m["actual_end"] - m["actual_start"]).total_seconds() / 60))
    kwh = m["gpus"] * 1.25 * minutes / 60
    base, actual = kwh * 8.44 * 1.25, kwh * 8.44 * 0.85
    got = tuple(float(m[k]) for k in ("baseline_cost", "actual_cost", "saved"))
    ok = all(abs(g - w) < 0.006 for g, w in zip(got, (base, actual, base - actual))) and got[2] > 0
    r.check(2, f"{n}: the measured saving matches a hand calculation", ok,
            f"baseline {got[0]:.2f}, actual {got[1]:.2f}, saved {got[2]:.2f} Rs (hand: {base:.2f}, {actual:.2f}, {base - actual:.2f}; {minutes} min, {m['gpus']} GPU)")


def phase_reconcile(ctx):
    """Restart the real cloud and the agent: the cloud must learn the real start and end of finished jobs from real accounting."""
    r = ctx.report
    ctx.cloud = cloud.start()
    ctx.agent = agentproc.start("autonomous", state="state.db" if ctx.mode == "full" else "state-stub.db")
    lab.until(lambda: all((cloud.managed(str(j)) or {}).get("state") == "COMPLETED" for j in ctx.finals.values()), 240, every=5,
              what="the cloud to learn that the finished jobs completed")
    expected = {"A1": 2, "A4": 1, "A5": 2, "F1": 1, "F2": 2}
    for n, j in ctx.finals.items():
        m, acc = cloud.managed(str(j)), lab.acct(j)
        r.check(2, f"{n}: the cloud's recorded start and end equal Slurm's accounting",
                int(m["actual_start"].timestamp()) == acc["start"] and int(m["actual_end"].timestamp()) == acc["end"], f"start {acc['start']}, end {acc['end']}")
        r.check(2, f"{n}: the GPU count the cloud holds equals what was requested", m["gpus"] == expected[n], f"{m['gpus']} (requested {expected[n]})")
        if ctx.mode == "full":
            check_measured(r, n, m)
    if ctx.mode == "full":
        m3 = cloud.managed(str(ctx.jobs["A3"]))
        r.check(5, "A3 stayed abandoned after the restart", m3["plan_status"] == "abandoned" and m3["note"] == "user_changed", m3["note"])
    r.save("cloud-audit-events", "\n".join(map(str, cloud.audit_events())))


PHASES = {"preflight": [phase_preflight], "shadow": [phase_preflight, phase_shadow],
          "all": [phase_preflight, phase_shadow, phase_release, phase_main, phase_reconcile]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["fast", "full"], default="fast")
    ap.add_argument("--only", choices=list(PHASES), default="all")
    ap.add_argument("--dry-run", action="store_true", help="print the schedule and stop")
    ap.add_argument("--now", action="store_true", help="full mode: do not idle until 13 minutes before the hour")
    args = ap.parse_args()
    now = datetime.now(cloud.IST)
    boundary = cloud.choose_boundary(now, MIN_LEAD)
    begin = now if args.mode == "fast" or args.now else max(now, boundary - MIN_LEAD - timedelta(minutes=1))
    finish = begin + timedelta(minutes=17) if args.mode == "fast" else boundary + timedelta(minutes=5)
    print(f"mode {args.mode}: starts {begin:%H:%M} IST, finishes about {finish:%H:%M} IST; the cheap window opens at {boundary:%H:%M} IST"
          + (" (fast mode does not wait for it)" if args.mode == "fast" else ""), flush=True)
    if args.dry_run:
        return 0
    if begin > now:
        time.sleep((begin - now).total_seconds())
    now = datetime.now(cloud.IST)
    report = Report()
    lab.cancel_all()
    cloud.RUN.mkdir(exist_ok=True)
    for f in ("state.db", "state-stub.db", "audit.log"):
        (cloud.RUN / f).unlink(missing_ok=True)
    key = cloud.seed(now, cloud.peak_hours(now, boundary))
    cloud.write_site_key(key)
    ctx = Ctx(report, args.mode, int(boundary.timestamp()), cloud.site_id(), key)
    ctx.cloud = cloud.start()
    try:
        for phase in PHASES[args.only]:
            print(f"--- {phase.__name__}", flush=True)
            phase(ctx)
    except Exception:
        report.notes.append("The run stopped early: " + traceback.format_exc().strip().splitlines()[-1])
        traceback.print_exc()
    finally:
        for p, stop in ((ctx.agent, agentproc.stop), (ctx.cloud, cloud.stop)):
            if p is not None:
                stop(p)
        if ctx.stub is not None and reachable(ctx.stub.base):
            ctx.stub.stop()
        lab.cancel_all()
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"{datetime.now():%Y-%m-%d}-end-to-end-{args.mode}-report.md"
        path.write_text(report.markdown(ctx), encoding="utf-8")
        print(f"report: {path}")
    return 0 if report.rows and all(r[2] for r in report.rows) and not any("stopped early" in n for n in report.notes) else 1


if __name__ == "__main__":
    sys.exit(main())
