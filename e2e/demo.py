"""The five-minute demo. Real Slurm (Docker), the real agent, the real cloud logic; only the tariff clock is compressed
(a time-lapse: every 2 minutes count as one tariff hour). Run `python e2e/demo.py`, open the printed URL, press Enter.
--auto runs without waiting for the presenter; --dry-run prints the storyboard; --no-vite leaves the dashboard server alone."""
import argparse
import os
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "backend")]
import agentproc  # noqa: E402
import cloud  # noqa: E402
import lab  # noqa: E402
import timelapse  # noqa: E402

DEMO = ROOT / "docs" / "demo"
FRONT_PORT = 5174  # the dev dashboard (5173) keeps talking to the normal API
URL = f"http://localhost:{FRONT_PORT}/#/live"
BLOCK_S = 75  # the busy cluster: two 4-GPU jobs that hold every GPU for this long
IST_ENV = {"TZ": "IST-5:30"}  # show Slurm's own times in IST, like the dashboard
LINES: list[str] = []


def say(text="", big=False):
    line = f"\n>>> {text}" if big else text
    print(line, flush=True)
    LINES.append(line)


def show(title, argv):
    """Run a Slurm command as the Operator and print it like a terminal would."""
    out = lab.dexec(argv, user="wsagent", env=IST_ENV, check=False).rstrip()
    say(f"$ {title}\n{out}")


def scontrol_lines(job, note=""):
    d = {}
    for tok in lab.dexec(["scontrol", "show", "job", str(job)], user="wsagent", env=IST_ENV).split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            d.setdefault(k, v)
    say(f"$ scontrol show job {job}   (only the interesting lines)\n  JobState={d.get('JobState')}  Reason={d.get('Reason')}  EligibleTime={d.get('EligibleTime')}{note}")


def wait_http(url, seconds=60):
    for _ in range(seconds):
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except OSError:
            time.sleep(1)
    return False


def start_vite():
    if wait_http(f"http://localhost:{FRONT_PORT}", 1):
        return None
    log = open(cloud.RUN / "vite-demo.log", "ab")
    # Live demo talks to the demo cloud; every other tab to the normal API (start it with `uvicorn app.main:app --port 8000`).
    env = {**os.environ, "VITE_LIVE_TARGET": cloud.BASE, "VITE_API_TARGET": os.environ.get("VITE_API_TARGET", "http://127.0.0.1:8000")}
    p = subprocess.Popen(["npx.cmd", "vite", "--port", str(FRONT_PORT), "--strictPort"], cwd=ROOT / "frontend", env=env, stdout=log, stderr=log)
    if not wait_http(f"http://localhost:{FRONT_PORT}", 60):
        p.kill()
        raise RuntimeError("the dashboard server did not start; see ~/.wattshift/e2e-run/vite-demo.log")
    return p


def capture(name):
    edge = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if Path(p).exists()), None)
    if edge is None:
        return
    (DEMO / "screens").mkdir(parents=True, exist_ok=True)
    subprocess.run([edge, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1440,1300", "--virtual-time-budget=8000",
                    f"--screenshot={DEMO / 'screens' / (name + '.png')}", URL], capture_output=True, timeout=90)
    say(f"(saved screenshot {name}.png)")


def storyboard():
    say("Storyboard (about 5 minutes): waiting jobs 0:00 | shadow mode 0:25 | switch to autonomous 0:50 | Slurm holds the jobs 1:20 | "
        "stop the agent and the cloud 1:40 | the jobs start by themselves ~3:30 | the cloud returns with the measured saving ~4:15")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-vite", action="store_true")
    ap.add_argument("--capture", action="store_true", help="save dashboard screenshots to docs/demo/screens (slower: the demo waits for each)")
    args = ap.parse_args()
    if args.dry_run:
        storyboard()
        return 0
    procs: dict[str, object] = {}
    try:
        # ---- preparation (not part of the five minutes) ----
        say("Preparing: the lab, the demo cloud and the dashboard...", big=True)
        lab.cancel_all()
        lab.wait_nodes_idle(60)
        now = datetime.now(cloud.IST)
        key = cloud.seed(now, timelapse.peak_pseudo_hours(now, timelapse.choose_boundary(now)), step=cloud.timedelta(minutes=1), hours=4)
        cloud.write_site_key(key)
        for f in ("state.db", "audit.log"):
            (cloud.RUN / f).unlink(missing_ok=True)
        sid = cloud.site_id()
        procs["cloud"] = cloud.start("demo_cloud_main.py")
        if not args.no_vite:
            procs["vite"] = start_vite()
        say(f"Ready. Open {URL} and put it on screen.", big=True)
        if not args.auto:
            input("Press Enter to start the demo... ")

        # ---- the demo ----
        t0 = time.time()
        now = datetime.now(cloud.IST)
        boundary = timelapse.choose_boundary(now, 330 if args.capture else timelapse.MIN_LEAD_S)  # captures take time: leave more room
        cloud.set_peak(timelapse.peak_pseudo_hours(now, boundary))
        b_epoch = int(boundary.timestamp())
        say(f"Electricity is EXPENSIVE right now. It turns CHEAP at {boundary:%H:%M:%S} IST.", big=True)
        say("(A time-lapse: every two minutes stand for one tariff hour. Everything else is real: Slurm, the agent, the cloud.)")
        blockers = [lab.sbatch("bob", f"demo-busy{i}", qos="normal", gres=4, minutes=2, sleep_s=BLOCK_S) for i in (1, 2)]
        lab.until(lambda: all(lab.state_of(b) == "RUNNING" for b in blockers), 60, what="the busy cluster")
        jobs = {n: lab.sbatch("alice", f"demo-{n}", gres=g, minutes=1, sleep_s=40) for n, g in (("A", 2), ("B", 1), ("C", 1))}
        say("Three GPU jobs are waiting behind a busy cluster:", big=True)
        show("squeue", ["squeue", "-o", "%.8i %.9j %.10T %.11r"])

        say("SHADOW MODE: Wattshift watches and plans. It must not touch Slurm.", big=True)
        cloud.api("POST", f"/sites/{sid}/mode", {"mode": "shadow"})
        procs["agent"] = agentproc.start("autonomous")
        lab.until(lambda: all(cloud.decisions(str(j)) for j in jobs.values()), 90, what="the cloud to plan all three jobs")
        say("The cloud has planned all three, and Slurm is untouched (no start time set):")
        scontrol_lines(jobs["A"])
        if args.capture:
            capture("01-shadow")

        say("Now switch the site to AUTONOMOUS. Click it on the dashboard.", big=True)
        try:
            lab.until(lambda: cloud.site_row()["mode"] == "autonomous", (8 if not args.capture else 3) if args.auto else 45, what="the presenter's click")
        except TimeoutError:
            say("(switching it for you)")
            cloud.api("POST", f"/sites/{sid}/mode", {"mode": "autonomous"})
        lab.until(lambda: all((lab.epoch(lab.show(j).get("EligibleTime")) or 0) >= b_epoch for j in jobs.values()), 90, what="Slurm to hold all three jobs")
        say(f"Wattshift set their start time in Slurm: {boundary:%H:%M:%S}, the moment electricity turns cheap.")
        scontrol_lines(jobs["A"], "   <- Slurm will not start it before this time")

        lab.wait_finished(blockers, 120)
        say("The busy jobs are done. The GPUs are FREE, and Slurm still holds our jobs:", big=True)
        show("sinfo", ["sinfo", "-N", "-h", "-o", "%N %T"])
        show("squeue", ["squeue", "-o", "%.8i %.9j %.10T %.11r %S"])
        if args.capture:
            capture("02-held")

        say("Now stop the agent AND the cloud completely.", big=True)
        agentproc.stop(procs.pop("agent"))
        cloud.stop(procs.pop("cloud"))
        say("Both are off. Slurm alone keeps the promise:")
        show("squeue", ["squeue", "-o", "%.8i %.9j %.10T %.11r %S"])
        while (left := b_epoch - time.time()) > 0:
            say(f"  ...the jobs start in {int(left)} s")
            time.sleep(min(10, max(1, left)))
        lab.until(lambda: any(lab.state_of(j) in ("RUNNING", "COMPLETED") for j in jobs.values()), 90, what="the first job to start")
        say("The jobs just started, with nothing of ours running:", big=True)
        show("squeue", ["squeue", "-o", "%.8i %.9j %.10T %.11r %S"])
        show(f"sacct   (the set time was {boundary:%H:%M:%S}; Slurm starts a job at the next scheduling cycle after it, never before)",
             ["sacct", "-X", "-j", ",".join(str(j) for j in jobs.values()), "-o", "JobID,JobName%10,State%10,Start"])

        say("Bring the cloud back. It learns what really happened from Slurm's own records.", big=True)
        procs["cloud"] = cloud.start("demo_cloud_main.py")
        procs["agent"] = agentproc.start("autonomous")
        lab.until(lambda: all((cloud.managed(str(j)) or {}).get("saved") is not None for j in jobs.values()), 150, what="the measured saving")
        s = cloud.api("GET", f"/sites/{sid}/view")["summary"]
        say(f"Measured saving: Rs {s['saved']:.2f} ({s['pct_saved']}% lower than starting when Slurm would have), across {s['jobs_measured']} jobs.", big=True)
        say("The percentage is what carries over to real jobs: these demo jobs run seconds, so the rupees are pennies.")
        say(f"That is about {time.time() - t0:.0f} seconds from the first job to the measured result.")
        if args.capture:
            capture("04-measured")
        return 0
    except Exception:
        traceback.print_exc()
        say("The live demo failed. Use the backup pack: docs/demo/storyboard.md, the screenshots, and the transcript.", big=True)
        return 1
    finally:
        for name, p in list(procs.items()):
            if p is not None:
                try:
                    (agentproc.stop if name == "agent" else cloud.stop)(p)
                except Exception:
                    pass
        lab.cancel_all()
        DEMO.mkdir(parents=True, exist_ok=True)
        (DEMO / "transcript.txt").write_text("\n".join(LINES), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
