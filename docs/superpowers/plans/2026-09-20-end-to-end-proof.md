# End-to-end proof on a real Slurm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, on a real Slurm (Docker), that the finished agent and the finished cloud do what the spec promises: a job the agent defers is held by Slurm and started on time, even with the agent and the cloud dead; a shadow agent changes nothing; `release-all` restores immediate start; a job changed by its owner is left alone; non-flex jobs are never touched. Also confirm the three Slurm details the agent's parsers assumed but never saw on a real cluster, and fix the parsers if they were wrong.

**Architecture:** A lab folder `e2e/` rebuilds the Spike 0 cluster from code (two nodes with four fake GPUs each, users `alice`, `bob`, and an Operator `wsagent`, strict `PrivateData`). The **real agent** (its own venv) reaches Slurm through `docker exec -u wsagent` (the `slurm.prefix` setting) and talks over real HTTP to the **real cloud code** running as a second API instance on the **test database**, with a synthetic tariff that turns cheap at a top-of-hour and constant synthetic prices. A scenario script drives the phases, asserts each spec success criterion, and writes a report. It has two modes: **fast** (about 17 minutes, any time of day; it does not wait for the cheap window, and the "Slurm starts a job on time with the agent and the cloud stopped" test uses a small stand-in cloud that hands out a start time 2.5 minutes away) and **full** (it idles until 13 minutes before a top-of-hour, then carries the real cloud's own decision all the way to a real start).

**Tech Stack:** Python 3.13 (host scripts run in the backend venv), Docker Desktop, the existing image `slurm-docker-cluster:26.05.2` (already local, no download), pytest for the pure helpers. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md` sections 14 (item 4) and 17. Findings: `docs/superpowers/spikes/2026-09-19-slurm-spike-findings.md` (including the "Harness detail" paragraph on fake GPUs).

## What is being proven (spec section 17, and where)

| # | Criterion | Phase that checks it |
|---|---|---|
| 1 | In shadow mode the agent reports jobs, the cloud logs plans, and **nothing in Slurm is modified** | Shadow |
| 2 | In autonomous mode flex jobs start in the planned window with no human action; non-flex jobs are untouched; arrays and dependent jobs are skipped | Release setup, Main |
| 3 | Killing the agent **and** the cloud after decisions does not stop or delay any job | Main (fast: a stand-in cloud hands out the start time; full: the real cloud's own decision) |
| 4 | `release-all` restores immediate start for every deferred job | Release |
| 5 | A job its owner changed is no longer managed | Main |
| 6 | The dashboard shows each job's state and modeled saving | **Not here** (the measurement plan) |
| F | The parsers' unverified assumptions hold on a real Slurm; `wattshift-agent check` passes as an Operator | Formats, Preflight |

Also measured and reported: the real gap between the start time the agent set and the real start.

## Global Constraints

- **Nothing outside this repo and Docker is changed.** The lab uses only images already on this machine (`slurm-docker-cluster:26.05.2`, `mariadb:12`). No downloads, no pulls. The cloud runs against `wattshift_tests` (the same isolation rule as the backend tests: the name must contain "test"); the dev database `wattshift` is never touched.
- **Everything runs from the host with argument lists** (`subprocess`, never a shell string), and multi-line scripts go into containers as files. Windows PowerShell 5.1 mangled quotes and added BOMs in Spike 0; this avoids it.
- **The real agent, unmodified**, runs in its own venv (`agent/.venv`); the cloud is the real backend code. What is synthetic: the tariff (hour-based, so it flips at a top-of-hour), constant electricity prices (so the tariff zone alone decides where a job goes), the 0-14 minute per-job start-time spread (switched off in the proof's cloud by a launcher that patches it in that process only; it is unit-tested elsewhere), and, in fast mode only, the stand-in cloud used for the checks whose names say "stand-in cloud". The report says all of this.
- **A failure is a finding.** If a check fails, decide whether the cause is the agent, the cloud, or the harness; fix the real cause test-first (in `agent/` or `backend/`), never loosen the check to make it pass.
- **Time.** Fast mode (the default) takes about 17 minutes at any time of day. Full mode idles until 13 minutes before a top-of-hour and then works for about 17 minutes, so its total is the idle wait plus about 17 minutes (launch it around :45 for about 20 minutes; `--dry-run` prints when it would start and finish). Either way the machine must stay awake and Docker running; tell the user before starting.
- No commits (the repo has none yet); commit only when the user asks. The existing suites must stay green: `agent` (182), `backend` (247).

## File structure

```
e2e/
  README.md                       how to run it, what it needs, what it proves
  conftest.py                     puts e2e/ and backend/ on sys.path for the pure tests
  lab.py                          the Docker Slurm: up / configure / down, exec helpers, job helpers
  cloud.py                        isolated cloud: synthetic tariff + prices, start/stop, DB reads, HTTP helper
  cloud_main.py                   launcher that starts the real cloud with the start-time spread switched off (proof only)
  stubcloud.py                    stand-in cloud for the fail-open test in fast mode
  agentproc.py                    run the real agent (config file, background loop, CLI calls, audit log parsing)
  capture_formats.py              record real Slurm outputs as agent test fixtures (Task 2)
  run.py                          the scenario and the report
  test_helpers.py                 pure tests for the tariff/boundary helpers
  slurm-lab/upstream/             a copy of github.com/giovtorres/slurm-docker-cluster (not committed)
  .run/                           logs, state, keys (not committed)
agent/tests/fixtures/real/*.txt   real Slurm outputs + index.json (Task 2)
agent/tests/test_real_formats.py  parser regression tests on those real outputs (Task 2)
docs/superpowers/e2e/             the report and raw outputs of the run (Task 5)
```

## How the timeline works

The cheap electricity window opens at the next top-of-hour (call it B). The synthetic tariff marks the hours from now until B as peak and every other hour as solar, and prices are constant, so the planner's cheapest bill is the first solar block. The proof's cloud has the per-job start-time spread switched off, so a deferred job's planned start is exactly B. Blocker jobs (two 4-GPU jobs, 80 seconds) keep the flex jobs **pending** long enough for the agent to see them, which is how real deferrals happen.

```
fast mode: about 17 minutes, any time (B may be up to an hour away; nothing waits for it)
  preflight 1 | shadow ~3 | release-all ~3.5 | real-cloud deferral checks + override ~3 | fail-open with a stand-in cloud ~5 | reconcile ~2
  the jobs the real cloud deferred to B are cancelled at the end

full mode: idles until 13 minutes before B, then about 17 minutes of work
  the same phases, but the deferred jobs are left to start at B with the agent and the cloud stopped
```

`python e2e\run.py --mode full --dry-run` prints when a full run would start and finish.

---

### Task 1: The lab as code

**Files:**
- Create: `e2e/lab.py`, `e2e/README.md`, `e2e/conftest.py`
- Modify: `.gitignore` (add two lines)

**Interfaces:**
- Produces (in `e2e/lab.py`):
  - constants `HERE, UPSTREAM, CTLD, WORKERS, USERS, EPOCH_ENV`; `LabError`
  - `run(cmd, *, cwd=None, check=True, timeout=300) -> CompletedProcess`; `compose(*args, check=True, timeout=600)`
  - `dexec(argv, *, container=CTLD, user=None, env=None, check=True, raw=False)` (`raw=True` returns the `CompletedProcess`); `script(text, *, container=CTLD, user=None) -> str`
  - `until(fn, timeout, every=2, what="") -> value` (raises `TimeoutError`)
  - `prepare_upstream()`, `up()`, `down(keep_images=True)`, `wait_nodes_idle(timeout=120)`
  - job helpers: `sbatch(user, name, *, qos="flex", gres=None, gpus=None, minutes=2, sleep_s=30, extra=()) -> int`, `show(jobid) -> dict`, `epoch(text) -> int | None`, `acct(jobid) -> dict | None` (`state, submit, eligible, start, end`), `state_of(jobid) -> str`, `wait_finished(jobids, timeout) -> int` (returns the latest end time), `cancel_all()`
  - `python e2e/lab.py {up|down|status|smoke}`

- [ ] **Step 1: Make sure Docker is running and nothing is left over**

Run (PowerShell):

```powershell
docker version --format '{{.Server.Version}}'
docker ps -a --format '{{.Names}}' | Select-String -Pattern 'slurm|mysql'
docker volume ls --format '{{.Name}}' | Select-String -Pattern 'slurm'
```

Expected: a server version and no Slurm containers or volumes. If the daemon is down, start it: `Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"` and poll `docker info` for up to five minutes. If old Slurm containers or volumes exist, stop and ask the user before removing them.

- [ ] **Step 2: Copy the upstream lab into the project (no download)**

The Spike 0 clone still exists in the session scratchpad. Copy it without its `.git` folder:

```powershell
$src = "C:\Users\aakur\AppData\Local\Temp\claude\C--Users-aakur-OneDrive-Desktop-WattShift\7da7f8ec-b58a-4921-82a4-3c2a5702cfdc\scratchpad\slurm-lab"
$dst = "C:\Users\aakur\OneDrive\Desktop\WattShift\e2e\slurm-lab\upstream"
New-Item -ItemType Directory -Force $dst | Out-Null
robocopy $src $dst /E /XD .git /XF put_*.sh helpers.ps1 lib.sh spike-notes.md "slurm.conf.spike" "gres.conf.spike" /NFL /NDL /NJH /NJS | Out-Null
Test-Path "$dst\docker-compose.yml"
```

Expected: `True`. If the scratchpad clone is gone, the only alternative is `git clone https://github.com/giovtorres/slurm-docker-cluster.git` into `$dst`, which is a download: **ask the user first**.

Append to `C:\Users\aakur\OneDrive\Desktop\WattShift\.gitignore`:

```
e2e/slurm-lab/upstream/
e2e/.run/
```

- [ ] **Step 3: Write `lab.py`**

Create `e2e/lab.py`:

```python
"""The Docker Slurm used by the end-to-end proof: bring it up, configure it, talk to it, tear it down.
Everything runs from the host with argument lists (never shell strings), so Windows quoting cannot bite."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPSTREAM = HERE / "slurm-lab" / "upstream"  # a copy of github.com/giovtorres/slurm-docker-cluster (not committed)
CTLD = "slurmctld"
WORKERS = ("slurm-cpu-worker-1", "slurm-cpu-worker-2")
USERS = {"alice": 2001, "bob": 2002, "wsagent": 2003}
EPOCH_ENV = {"TZ": "UTC", "SLURM_TIME_FORMAT": "%s"}

OVERRIDE = """services:
  cpu-worker:
    volumes:
      - ./entrypoint.workers.sh:/usr/local/bin/docker-entrypoint.sh:ro
"""
ACCOUNTS = """sacctmgr -i add account labs Description=e2e Organization=wattshift || true
sacctmgr -i add qos flex || true
for u in alice bob; do
  sacctmgr -i add user $u Account=labs || true
  sacctmgr -i modify user $u set qos+=flex || true
done
sacctmgr -i add user wsagent Account=labs AdminLevel=Operator || true
sacctmgr show user alice,bob,wsagent format=User,Account,AdminLevel,QOS -P
"""
CONTROLLER_CONFIG = """set -e
printf 'Name=gpu File=/dev/null,/dev/zero,/dev/full,/dev/urandom\\n' > /etc/slurm/gres.conf
sed -i '/^PrivateData/d' /etc/slurm/slurm.conf
echo 'PrivateData=jobs,usage,users' >> /etc/slurm/slurm.conf
grep -E '^(GresTypes|PrivateData|MinJobAge|SchedulerType)' /etc/slurm/slurm.conf
"""
TERMINAL = {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "OUT_OF_MEMORY", "NODE_FAIL"}


class LabError(RuntimeError):
    pass


def run(cmd, *, cwd=None, check=True, timeout=300) -> subprocess.CompletedProcess:
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=cwd, timeout=timeout)
    if check and p.returncode != 0:
        raise LabError(f"{' '.join(map(str, cmd))[:140]} failed ({p.returncode}): {(p.stderr or p.stdout).strip()[:500]}")
    return p


def compose(*args, check=True, timeout=600):
    return run(["docker", "compose", *args], cwd=UPSTREAM, check=check, timeout=timeout)


def dexec(argv, *, container=CTLD, user=None, env=None, check=True, raw=False):
    cmd = ["docker", "exec", "-w", "/tmp"]
    if user:
        cmd += ["-u", user]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    p = run([*cmd, container, *argv], check=check)
    return p if raw else p.stdout


def script(text: str, *, container=CTLD, user=None) -> str:
    """Run a multi-line bash script in a container. It goes in as a file (no BOM, Unix line endings) so quoting never matters."""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, newline="\n", encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        run(["docker", "cp", path, f"{container}:/tmp/_e2e.sh"])
        return dexec(["bash", "/tmp/_e2e.sh"], container=container, user=user)
    finally:
        os.unlink(path)


def until(fn, timeout, every=2, what=""):
    """Poll fn() until it returns something truthy; return it. Raises TimeoutError naming what we waited for."""
    deadline = time.time() + timeout
    while True:
        value = fn()
        if value:
            return value
        if time.time() > deadline:
            raise TimeoutError(f"timed out after {timeout} s waiting for {what or fn}")
        time.sleep(every)


# --- bring the cluster up ------------------------------------------------------------------------------------------
def prepare_upstream() -> None:
    if not (UPSTREAM / "docker-compose.yml").exists():
        raise LabError("copy the slurm-docker-cluster clone into e2e/slurm-lab/upstream first (see README)")
    orig = (UPSTREAM / "docker-entrypoint.sh").read_text(encoding="utf-8")
    patched = orig.replace('--conf "Feature=cpu"', '--conf "Feature=cpu Gres=gpu:4"')  # workers must declare their fake GPUs
    if patched == orig:
        raise LabError("the upstream entrypoint changed: update the Gres patch in lab.prepare_upstream()")
    (UPSTREAM / "entrypoint.workers.sh").write_bytes(patched.replace("\r\n", "\n").encode("utf-8"))
    (UPSTREAM / "docker-compose.override.yml").write_text(OVERRIDE, encoding="utf-8")


def wait_nodes_idle(timeout=120) -> str:
    def nodes():
        out = dexec(["sinfo", "-N", "-h", "-o", "%N %T %G"], check=False).strip()
        rows = [r.split() for r in out.splitlines() if r.strip()]
        return out if len(rows) == 2 and all(r[1] == "idle" and "gpu:4" in r[2] for r in rows) else None

    try:
        return until(nodes, timeout, what="two idle nodes with 4 GPUs each")
    except TimeoutError:
        dexec(["scontrol", "update", "NodeName=c[1-2]", "State=RESUME"], check=False)  # Spike 0: nodes can come up INVALID_REG
        return until(nodes, 60, what="two idle nodes with 4 GPUs each (after RESUME)")


def up() -> None:
    prepare_upstream()
    compose("up", "-d", "mysql", "slurmdbd", "slurmctld")
    until(lambda: "slurm" in dexec(["scontrol", "--version"], check=False), 180, what="the controller")
    print(script(CONTROLLER_CONFIG))
    compose("restart", "slurmctld")
    until(lambda: "slurm" in dexec(["scontrol", "--version"], check=False), 120, what="the controller after restart")
    compose("up", "-d", "cpu-worker")  # started only now, so the nodes register with the right GPU config the first time
    for c in (CTLD, *WORKERS):
        until(lambda c=c: dexec(["true"], container=c, check=False) is not None, 60, what=f"container {c}")
        for user, uid in USERS.items():
            dexec(["bash", "-c", f"id {user} >/dev/null 2>&1 || useradd -m -u {uid} {user}"], container=c)
    print(script(ACCOUNTS))
    print(wait_nodes_idle())


def down(keep_images=True) -> None:
    compose("down", "-v", check=False)  # removes containers and volumes; images stay (the caller decides about those)


# --- talking to it ---------------------------------------------------------------------------------------------------
def sbatch(user, name, *, qos="flex", gres=None, gpus=None, minutes=2, sleep_s=30, extra=()) -> int:
    argv = ["sbatch", "--parsable", "-p", "cpu", f"--qos={qos}", "-J", name, "-t", str(minutes), *extra]
    if gres:
        argv.append(f"--gres=gpu:{gres}")
    if gpus:
        argv.append(f"--gpus={gpus}")
    argv.append(f"--wrap=sleep {sleep_s}")
    return int(dexec(argv, user=user).strip().split(";")[0])


def epoch(text):
    return int(text) if text and text.isdigit() else None


def show(jobid) -> dict:
    """`scontrol show job` as a dict of its key=value tokens (first occurrence wins), times as epoch seconds."""
    out = dexec(["scontrol", "show", "job", str(jobid)], user="wsagent", env=EPOCH_ENV)
    d: dict = {}
    for tok in out.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            d.setdefault(k, v)
    return d


def acct(jobid):
    out = dexec(["sacct", "-j", str(jobid), "-P", "-X", "-n", "-S", "now-1days", "-o", "JobID,State,Submit,Eligible,Start,End"],
                user="wsagent", env=EPOCH_ENV, check=False).strip()
    if not out:
        return None
    _, state, submit, eligible, start, end = out.splitlines()[0].split("|")
    return {"state": state.split()[0], "submit": epoch(submit), "eligible": epoch(eligible), "start": epoch(start), "end": epoch(end)}


def state_of(jobid) -> str:
    out = dexec(["squeue", "-h", "-j", str(jobid), "-o", "%T"], user="wsagent", check=False).strip()
    return out or (acct(jobid) or {}).get("state", "UNKNOWN")


def wait_finished(jobids, timeout) -> int:
    """Wait until every job is in a terminal state; return the latest end time (epoch)."""
    def done():
        rows = [acct(j) for j in jobids]
        return rows if all(r and r["state"] in TERMINAL and r["end"] for r in rows) else None

    return max(r["end"] for r in until(done, timeout, every=5, what=f"jobs {list(jobids)} to finish"))


def cancel_all() -> None:
    for u in ("alice", "bob"):
        dexec(["scancel", f"--user={u}"], check=False)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "up":
        up()
    elif cmd == "down":
        down()
    elif cmd == "smoke":
        j = sbatch("alice", "e2e-smoke", gres=2, minutes=2, sleep_s=5)
        until(lambda: (acct(j) or {}).get("state") == "COMPLETED", 120, what="the smoke job")
        print(f"smoke job {j}: {acct(j)}")
    else:
        print(dexec(["sinfo", "-N", "-o", "%N %T %G"], check=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Write `README.md` and `conftest.py`**

Create `e2e/conftest.py`:

```python
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "backend")]
```

Create `e2e/README.md` with these sections, in plain language: what this proves (the table above); what it needs (Docker Desktop running, the local images, the agent venv `agent/.venv`, the backend venv, `TEST_DATABASE_URL` in `~/.wattshift/.env`); `lab.py up|down|smoke|status`; `run.py` in its two modes (fast: about 17 minutes any time; full: waits for a top-of-hour and carries the real cloud's decision to a real start) and why the full mode has to wait (the tariff only changes on the hour); what is synthetic (tariff, prices) and what is real (agent, cloud code, Slurm); where the report goes.

- [ ] **Step 5: Bring the lab up and run the smoke job**

Run:

```powershell
cd C:\Users\aakur\OneDrive\Desktop\WattShift
.\backend\.venv\Scripts\python e2e\lab.py up
.\backend\.venv\Scripts\python e2e\lab.py smoke
```

Expected: `up` prints the config lines (`GresTypes=gpu`, `PrivateData=jobs,usage,users`), the three users (`wsagent` with `AdminLevel=Operator`, `alice`/`bob` with `flex`), and two nodes `c1 idle gpu:4`, `c2 idle gpu:4`; `smoke` prints a `COMPLETED` accounting row. If a node is `inval` or `drain`, `wait_nodes_idle` already tries `State=RESUME` once; if it still fails, read `docker compose logs slurmctld cpu-worker` from `e2e/slurm-lab/upstream`, fix the cause, and do not loop.

---

### Task 2: Real Slurm outputs as fixtures, and the three unverified assumptions

**Files:**
- Create: `e2e/capture_formats.py`, `agent/tests/fixtures/real/*` (generated), `agent/tests/test_real_formats.py`
- Modify (only if the run shows a mismatch): `agent/wattshift_agent/parse.py`, `agent/wattshift_agent/slurm.py`, `agent/tests/fakeslurm.py`, `agent/tests/fixtures/raw/*`

**Interfaces:**
- Consumes: the running lab and `lab.py` helpers (Task 1); the agent's exact command lists (`wattshift_agent.slurm.QUEUE_FORMAT`, `ACCT_FIELDS`).
- Produces: `agent/tests/fixtures/real/` with `index.json` (label to job id) and raw outputs of the agent's own commands on a real Slurm; parser tests that read them.

The three assumptions being tested: the exact `squeue -o` field set (`%i|%T|%u|%a|%q|%P|%V|%S|%r|%j` with epoch times), `sacct -S now-14days` (accepted, and returns the rows), and `TresPerJob` for `--gpus=N` jobs. Everything else the parsers rely on was already seen in Spike 0.

- [ ] **Step 1: Write the capture script**

Create `e2e/capture_formats.py`:

```python
"""Record what a REAL Slurm prints for the exact commands the agent sends, as test fixtures (agent/tests/fixtures/real)."""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lab  # noqa: E402

OUT = HERE.parent / "agent" / "tests" / "fixtures" / "real"
QUEUE_FORMAT = "%i|%T|%u|%a|%q|%P|%V|%S|%r|%j"  # must equal wattshift_agent.slurm.QUEUE_FORMAT (asserted below)
ACCT_FIELDS = "JobID,State,Start,End,ElapsedRaw,AllocTRES"


def agent(argv, *, check=True):
    """Run a Slurm command exactly like the agent does: as the Operator, UTC, epoch times."""
    return lab.dexec(argv, user="wsagent", env=lab.EPOCH_ENV, check=check, raw=True)


def save(name, text):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    sys.path.insert(0, str(HERE.parent / "agent"))
    from wattshift_agent.slurm import ACCT_FIELDS as AGENT_ACCT, QUEUE_FORMAT as AGENT_QUEUE
    assert (AGENT_QUEUE, AGENT_ACCT) == (QUEUE_FORMAT, ACCT_FIELDS), "the agent's command formats changed: update this script"
    lab.cancel_all()
    ids: dict[str, int] = {}

    # finished jobs for sacct: a completed one, a failed one, a timed-out one, one cancelled while running
    ids["done"] = lab.sbatch("alice", "e2e-done", gres=1, minutes=2, sleep_s=3)
    ids["failed"] = int(lab.dexec(["sbatch", "--parsable", "-p", "cpu", "--qos=flex", "-J", "e2e-failed", "-t", "2", "--wrap=exit 3"], user="alice").strip())
    lab.until(lambda: lab.state_of(ids["done"]) == "COMPLETED", 90, what="the completed job")
    ids["timeout"] = lab.sbatch("alice", "e2e-timeout", gres=1, minutes=1, sleep_s=200)
    ids["cancel_run"] = lab.sbatch("alice", "e2e-cancel-run", gres=1, minutes=3, sleep_s=200)
    lab.until(lambda: lab.state_of(ids["cancel_run"]) == "RUNNING", 60, what="the job to cancel while running")
    lab.dexec(["scancel", str(ids["cancel_run"])], user="alice")
    lab.until(lambda: lab.state_of(ids["timeout"]) == "TIMEOUT", 150, every=5, what="the job to time out")

    # fill every GPU so later jobs stay pending
    blockers = [lab.sbatch("bob", f"e2e-block{i}", qos="normal", gres=4, minutes=4, sleep_s=200) for i in (1, 2)]
    lab.until(lambda: all(lab.state_of(b) == "RUNNING" for b in blockers), 90, what="the blockers")

    ids["gres3"] = lab.sbatch("alice", "e2e-gres3", gres=3, minutes=5)
    ids["gpus2"] = lab.sbatch("alice", "e2e-gpus2", gpus=2, minutes=5)
    ids["begin"] = lab.sbatch("alice", "e2e-begin", gres=1, minutes=5, extra=["--begin=now+2hours"])
    ids["dependent"] = lab.sbatch("alice", "e2e-dependent", gres=1, minutes=5, extra=[f"--dependency=afterok:{ids['gres3']}"])
    ids["array"] = lab.sbatch("alice", "e2e-array", gres=1, minutes=5, extra=["--array=1-3"])
    ids["barname"] = lab.sbatch("alice", "e2e name | with bar", gres=1, minutes=5)
    ids["normal"] = lab.sbatch("bob", "e2e-normal", qos="normal", gres=1, minutes=5)
    ids["cancel_pend"] = lab.sbatch("alice", "e2e-cancel-pend", gres=1, minutes=5)
    lab.dexec(["scancel", str(ids["cancel_pend"])], user="alice")
    time.sleep(35)  # Slurm's own start estimate is N/A for the first ~30 s (Spike 0): record it settled, too

    save("squeue_all.txt", agent(["squeue", "-h", "-t", "PENDING,RUNNING", "-o", QUEUE_FORMAT]).stdout)
    for label in ("gres3", "gpus2", "begin", "dependent", "array", "barname", "normal"):
        save(f"scontrol_{label}.txt", agent(["scontrol", "show", "job", str(ids[label])]).stdout)
    finished = [ids[k] for k in ("done", "failed", "timeout", "cancel_run", "cancel_pend")]
    save("sacct_finished.txt", agent(["sacct", "-j", ",".join(map(str, finished)), "-P", "-X", "-n", "-S", "now-14days", "-o", ACCT_FIELDS]).stdout)
    bad = agent(["scontrol", "show", "job", "999999"], check=False)
    save("scontrol_invalid.txt", f"exit={bad.returncode}\nstdout={bad.stdout!r}\nstderr={bad.stderr!r}\n")
    (OUT / "index.json").write_text(json.dumps(ids, indent=2), encoding="utf-8", newline="\n")
    lab.cancel_all()
    print(f"saved {len(list(OUT.iterdir()))} files to {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and read every output**

Run: `.\backend\.venv\Scripts\python e2e\capture_formats.py`
Then open each file in `agent/tests/fixtures/real/` and look at it. Things to check by eye: `squeue_all.txt` has 10-field pipe rows; times are 10-digit numbers or `N/A`; the array shows as `<id>_[1-3]`; the job named `e2e name | with bar` has its `|` inside the last field; `scontrol_gres3.txt` has `TresPerNode=gres/gpu:3`; `scontrol_gpus2.txt` shows how `--gpus=2` appears (`TresPerJob=gres/gpu:2`, or something else: **write down exactly which key**); `scontrol_dependent.txt` has a non-null `Dependency=`; `sacct_finished.txt` has five rows with `COMPLETED`, `FAILED`, `TIMEOUT`, and two `CANCELLED by ...`; `scontrol_invalid.txt` shows the exit code and the exact error text for an unknown job.

- [ ] **Step 3: Write the real-format tests**

Create `agent/tests/test_real_formats.py`:

```python
"""The agent's parsers against outputs recorded from a REAL Slurm (e2e/capture_formats.py)."""
import json
from pathlib import Path

import pytest

from wattshift_agent import parse
from wattshift_agent.parse import parse_acct, parse_detail, parse_queue

REAL = Path(__file__).parent / "fixtures" / "real"
pytestmark = pytest.mark.skipif(not (REAL / "index.json").exists(), reason="real fixtures not recorded")


def ids():
    return json.loads((REAL / "index.json").read_text())


def read(name):
    return (REAL / name).read_text(encoding="utf-8")


def rows():
    return {r.name: r for r in parse_queue(read("squeue_all.txt"))}


def test_the_queue_parses_and_times_are_epoch_seconds():
    by_name = rows()
    assert {"e2e-gres3", "e2e-gpus2", "e2e-normal", "e2e-array", "e2e-begin", "e2e-dependent"} <= set(by_name)
    r = by_name["e2e-gres3"]
    assert (r.state, r.user, r.account, r.qos, r.partition) == ("PENDING", "alice", "labs", "flex", "cpu")
    assert isinstance(r.submit, int) and r.submit > 1_700_000_000  # %V came back as epoch seconds
    assert by_name["e2e-normal"].qos == "normal"


def test_special_rows():
    by_name = rows()
    assert "_" in by_name["e2e-array"].ref  # a pending array shows as <id>_[1-3]
    assert by_name["e2e-dependent"].reason == "Dependency"
    assert by_name["e2e-begin"].reason == "BeginTime" and isinstance(by_name["e2e-begin"].start, int)  # a user's --begin shows in %S
    assert "e2e name | with bar" in by_name  # a | inside the job name survives (name is the last field)


def test_running_blockers_are_listed_with_a_real_start():
    running = [r for r in rows().values() if r.state == "RUNNING"]
    assert len(running) == 2 and all(isinstance(r.start, int) for r in running)


def test_gpu_counts_from_real_scontrol_output():
    assert parse_detail(read("scontrol_gres3.txt"), str(ids()["gres3"])).gpus == 3
    assert parse_detail(read("scontrol_gpus2.txt"), str(ids()["gpus2"])).gpus == 2  # --gpus style (TresPerJob or as recorded)


def test_detail_flags_from_real_scontrol_output():
    d = parse_detail(read("scontrol_dependent.txt"), str(ids()["dependent"]))
    assert d.dependency is True and d.reason == "Dependency"
    b = parse_detail(read("scontrol_begin.txt"), str(ids()["begin"]))
    assert b.reason == "BeginTime" and isinstance(b.eligible, int) and b.eligible > b.submit
    g = parse_detail(read("scontrol_gres3.txt"), str(ids()["gres3"]))
    assert g.dependency is False and g.restarts == 0 and g.time_limit_min == 5
    assert parse_detail(read("scontrol_array.txt"), str(ids()["array"])).array is True


def test_accounting_rows_from_real_sacct_output():
    rows_ = {r.ref: r for r in parse_acct(read("sacct_finished.txt"))}
    i = ids()
    assert rows_[str(i["done"])].state == "COMPLETED" and rows_[str(i["done"])].end
    assert rows_[str(i["failed"])].state == "FAILED"
    assert rows_[str(i["timeout"])].state == "TIMEOUT"
    assert rows_[str(i["cancel_run"])].state == "CANCELLED" and rows_[str(i["cancel_run"])].start
    assert rows_[str(i["cancel_pend"])].state == "CANCELLED" and rows_[str(i["cancel_pend"])].start is None  # never started


def test_an_unknown_job_is_reported_the_way_the_facade_expects():
    text = read("scontrol_invalid.txt")
    assert "exit=1" in text and "Invalid job id" in text  # slurm.Slurm.detail() treats exactly this as "job is gone"
```

- [ ] **Step 4: Run them and act on what they say**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_real_formats.py -q`

Expected if the assumptions held: all pass. **If any fail, that is a finding, not a test to loosen.** The procedure for each failure:
1. Look at the real fixture line and compare with what the parser assumes.
2. Change only the parser or the facade (`parse.py`, `slurm.py`), the smallest possible change, and only the code, not the test. If the real format differs from the synthetic fixtures in `agent/tests/fixtures/raw/` or from `tests/fakeslurm.py`, update those to the real format too so the fake keeps speaking what Slurm really speaks.
3. Re-run the whole agent suite (`182` plus these) and confirm green.
4. Note the mismatch and the fix in the report (Task 5).

For `--gpus=2`: if `TresPerJob` is not the key, `gpus_from` must read the key the fixture shows (it is one of `TresPerJob`, `TresPerTask`, or only `ReqTRES`); add a `gpus_from` case in `test_parse.py` with the recorded line.

- [ ] **Step 5: Full agent suite**

Run: `cd agent; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider`
Expected: all green (182 plus the new real-format tests).

---

### Task 3: The cloud side of the proof

**Files:**
- Create: `e2e/cloud.py`, `e2e/agentproc.py`, `e2e/test_helpers.py`

**Interfaces:**
- Produces (in `e2e/cloud.py`): `IST`, `PORT`, `BASE`, `RUN`; `db_url()`; `choose_boundary(now, min_lead=13 min) -> datetime`; `peak_hours(now, boundary) -> set[int]`; `hour_rules(peak) -> list[dict]`; `seed(now, peak) -> str` (recreates the test database, returns the site key, writes `.run/site_id.txt`); `site_id() -> str`; `start() -> Popen`, `stop(p)`, `is_up() -> bool`; `api(method, path, body=None)`; DB reads `managed(ref)`, `managed_prefix(prefix)`, `decisions(ref)`, `site_row()`, `audit_events()`.
- Produces (in `e2e/agentproc.py`): `PREFIX`, `write_config(mode, url=cloud.BASE, state="state.db")`, `cli(mode, *args, url=cloud.BASE, state="state.db")`, `start(mode, url=cloud.BASE, state="state.db") -> Popen`, `stop(p)`, `audit_commands() -> list[list[str]]`.
- Produces (in `e2e/cloud_main.py`): `python e2e/cloud_main.py <port>`, the real cloud with `allocator.jitter_minutes` patched to 0 in that process.
- Produces (in `e2e/stubcloud.py`): `StubCloud(key, delay_s=150, port=8101)` with `.targets` (refs that get a decision), `.decided`, `.applied`, `.bodies`, `.base`, `.handle(body)`, `.start()`, `.stop()`. It speaks the `/agent/v1/sync` contract, hands each target job the same start time (`now + delay_s`, fixed at first sight) and keeps re-sending it until the agent reports it applied, like the real cloud.

- [ ] **Step 1: Write the failing tests for the pure helpers**

Create `e2e/test_helpers.py`:

```python
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

import cloud
import stubcloud

IST = cloud.IST


def t(h, m):
    return datetime(2026, 9, 21, h, m, tzinfo=IST)


def test_hour_rules_cover_every_hour_once_and_mark_the_peak():
    rules = cloud.hour_rules({22, 23, 0})
    zone = {h: r["zone"] for r in rules for h in range(r["start_hour"], r["end_hour"])}
    assert sorted(zone) == list(range(24)) and sum(r["end_hour"] - r["start_hour"] for r in rules) == 24
    assert {h for h, z in zone.items() if z == "peak"} == {22, 23, 0}
    assert all(r["season"] is None for r in rules)
    assert {r["adj_pct"] for r in rules if r["zone"] == "peak"} == {25} and {r["adj_pct"] for r in rules if r["zone"] == "solar"} == {-15}


def test_the_rules_load_through_the_clouds_own_tariff_code():
    from app.catalogue import _rules_from_json
    from app.tariff import tod_multiplier

    rules = _rules_from_json(cloud.hour_rules({10}))
    assert tod_multiplier(t(10, 30), rules) == 1.25 and tod_multiplier(t(11, 0), rules) == 0.85


def test_choose_boundary_keeps_the_minimum_lead():
    assert cloud.choose_boundary(t(10, 5)) == t(11, 0)  # 55 minutes of lead
    assert cloud.choose_boundary(t(10, 47)) == t(11, 0)  # exactly 13
    assert cloud.choose_boundary(t(10, 48)) == t(12, 0)  # 12 minutes: too tight, take the next one
    assert cloud.choose_boundary(t(23, 30)) == t(23, 30).replace(hour=0, minute=0) + timedelta(days=1)  # wraps midnight


def test_peak_hours_run_from_now_to_the_boundary():
    assert cloud.peak_hours(t(10, 5), t(11, 0)) == {10}
    assert cloud.peak_hours(t(10, 40), t(12, 0)) == {10, 11}
    assert cloud.peak_hours(t(23, 10), t(23, 10).replace(hour=1, minute=0) + timedelta(days=1)) == {23, 0}


def test_the_stand_in_hands_out_one_start_time_until_the_agent_confirms_it():
    stub = stubcloud.StubCloud("k", delay_s=100)
    stub.targets = {"7"}
    body = {"jobs": [{"ref": "7", "state": "PENDING"}, {"ref": "8", "state": "PENDING"}], "applied": []}
    first = stub.handle(body)
    assert [d["ref"] for d in first["decisions"]] == ["7"]  # only the target job gets a decision
    assert stub.handle(body)["decisions"] == first["decisions"]  # the same time again, not a new one
    stub.handle({"jobs": [], "applied": [{"ref": "7", "start_at": "x", "ok": True}]})
    assert stub.handle(body)["decisions"] == []  # confirmed: stop sending it
    assert stub.handle(body)["next_poll_s"] == 10 and stub.handle(body)["release_all"] is False


def test_the_stand_in_start_time_is_in_the_future_and_in_the_agents_format():
    stub = stubcloud.StubCloud("k", delay_s=150)
    stub.targets = {"7"}
    out = stub.handle({"jobs": [{"ref": "7", "state": "PENDING"}], "applied": []})["decisions"][0]["start_at"]
    assert out.endswith("Z") and len(out) == 20  # 2026-09-21T14:13:20Z
    assert stub.decided["7"] > __import__("time").time() + 100


def test_the_stand_in_checks_the_site_key_over_http():
    stub = stubcloud.StubCloud("secret", port=0)
    stub.start()
    try:
        def post(key):
            req = urllib.request.Request(stub.base + "/agent/v1/sync", data=json.dumps({"jobs": [], "applied": []}).encode(),
                                         method="POST", headers={"Content-Type": "application/json", "X-Site-Key": key})
            return urllib.request.urlopen(req, timeout=5)

        assert json.loads(post("secret").read())["next_poll_s"] == 10
        with pytest.raises(urllib.error.HTTPError) as e:
            post("wrong")
        assert e.value.code == 401
        assert json.loads(urllib.request.urlopen(stub.base + "/health", timeout=5).read()) == {"ok": True}
    finally:
        stub.stop()
    with pytest.raises(OSError):
        urllib.request.urlopen(stub.base + "/health", timeout=2)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `.\backend\.venv\Scripts\python -m pytest e2e/test_helpers.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'cloud'`.

- [ ] **Step 3: Write `cloud.py`**

Create `e2e/cloud.py`:

```python
"""The cloud side of the end-to-end proof: an isolated API + database, and a synthetic tariff and prices."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import Session

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BACKEND = ROOT / "backend"
RUN = HERE / ".run"
PORT = 8100
BASE = f"http://127.0.0.1:{PORT}"
IST = timezone(timedelta(hours=5, minutes=30))
STEP = timedelta(minutes=15)
PRICE = 5000.0  # Rs/MWh, constant: with equal prices the tariff zone alone decides where a job goes


def db_url() -> str:
    url = dotenv_values(Path.home() / ".wattshift" / ".env").get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("TEST_DATABASE_URL is missing from ~/.wattshift/.env")
    return url


# --- the synthetic tariff and the timeline -----------------------------------------------------------------------------
def choose_boundary(now: datetime, min_lead: timedelta = timedelta(minutes=13)) -> datetime:
    """The next top-of-hour (IST) that leaves at least min_lead to run the earlier phases; otherwise the one after."""
    b = now.astimezone(IST).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return b if b - now >= min_lead else b + timedelta(hours=1)


def peak_hours(now: datetime, boundary: datetime) -> set[int]:
    """IST hours from the hour containing `now` up to (not including) the boundary: expensive now, cheap after."""
    t = now.astimezone(IST).replace(minute=0, second=0, microsecond=0)
    hours: set[int] = set()
    while t < boundary:
        hours.add(t.hour)
        t += timedelta(hours=1)
    return hours


def hour_rules(peak: set[int]) -> list[dict]:
    """A tariff with 'peak' (+25%) in the given IST hours and 'solar' (-15%) in every other hour, all year."""
    rules: list[dict] = []
    for h in range(24):
        zone, pct = ("peak", 25) if h in peak else ("solar", -15)
        if rules and rules[-1]["zone"] == zone and rules[-1]["end_hour"] == h:
            rules[-1]["end_hour"] = h + 1
        else:
            rules.append({"zone": zone, "start_hour": h, "end_hour": h + 1, "season": None, "adj_pct": pct})
    return rules


# --- seeding, running and reading the isolated cloud ------------------------------------------------------------
def seed(now: datetime, peak: set[int]) -> str:
    """Recreate the TEST database with one E2E site (shadow mode) and return the site key. Test database only."""
    from app import db, sites
    from app.models import PriceSignal, TariffCatalogue

    RUN.mkdir(exist_ok=True)
    engine = db.make_engine(db_url())
    with engine.begin() as c:
        name = c.exec_driver_sql("select current_database()").scalar()
        assert "test" in name, f"refusing to seed database {name!r}"
    db.Base.metadata.drop_all(engine)
    db.Base.metadata.create_all(engine)
    today = now.astimezone(IST).date()
    with Session(engine) as s, s.begin():
        s.add(TariffCatalogue(
            utility="TEST", category="E2E", valid_from=today - timedelta(days=1), valid_until=today + timedelta(days=365),
            rules=hour_rules(peak), base_rate=8.44, verified=False, source="end-to-end proof: synthetic tariff",
        ))
        t = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        end = t + timedelta(hours=76)
        while t < end:
            s.add(PriceSignal(ts=t, price_rs_per_mwh=PRICE, source="iex_dam"))
            t += STEP
        site, key = sites.create_site(s, "E2E", "Lab", gpus=8, shift_capacity_share=1.0, utility="TEST", tariff_category="E2E", mode="shadow")
        (RUN / "site_id.txt").write_text(str(site.id))
    return key


def site_id() -> str:
    return (RUN / "site_id.txt").read_text().strip()


def is_up() -> bool:
    try:
        urllib.request.urlopen(BASE + "/health", timeout=2)
        return True
    except OSError:
        return False


def start() -> subprocess.Popen:
    RUN.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("SCHEDULER", "DEMO_MODE", "API_KEY")}
    env.update(DATABASE_URL=db_url(), PYTHONPATH=str(BACKEND))
    log = open(RUN / "cloud.log", "ab")
    p = subprocess.Popen([sys.executable, str(HERE / "cloud_main.py"), str(PORT)], cwd=BACKEND, env=env, stdout=log, stderr=log)
    for _ in range(60):
        if is_up():
            return p
        time.sleep(1)
    p.kill()
    raise RuntimeError("the cloud did not start; see e2e/.run/cloud.log")


def stop(p: subprocess.Popen) -> None:
    p.kill()
    p.wait(timeout=20)


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


@contextmanager
def session():
    from app import db

    with Session(db.make_engine(db_url())) as s:
        yield s


def _row(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def managed(ref: str) -> dict | None:
    from app.models import ManagedJob

    with session() as s:
        row = s.scalar(select(ManagedJob).where(ManagedJob.ref == ref))
        return _row(row) if row else None


def managed_prefix(prefix: str) -> dict | None:
    """The managed job whose ref starts with `prefix` (a pending array's ref is like 41_[1-3])."""
    from app.models import ManagedJob

    with session() as s:
        row = s.scalar(select(ManagedJob).where(ManagedJob.ref.like(prefix + "%")))
        return _row(row) if row else None


def decisions(ref: str) -> list[dict]:
    from app.models import Decision, ManagedJob

    with session() as s:
        rows = s.scalars(select(Decision).join(ManagedJob, ManagedJob.id == Decision.managed_job_id).where(ManagedJob.ref == ref).order_by(Decision.id))
        return [_row(r) for r in rows]


def site_row() -> dict:
    from app.models import Site

    with session() as s:
        return _row(s.get(Site, site_id()))


def audit_events() -> list[tuple]:
    from app.models import AuditLog

    with session() as s:
        return [(a.actor, a.event, a.ref) for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]
```

`s.get(Site, site_id())` needs a UUID: use `import uuid` and `uuid.UUID(site_id())` if SQLAlchemy complains; keep whichever works.

- [ ] **Step 3a: Write `cloud_main.py` and `stubcloud.py`**

Create `e2e/cloud_main.py`:

```python
"""Start the real cloud for the end-to-end proof, with the per-job start-time spread (0-14 minutes, an anti-herd
feature that is unit-tested elsewhere) switched off IN THIS PROCESS ONLY, so a deferred job's planned start is exactly the
top of the hour and the proof does not wait up to 15 extra minutes. Production code is not changed."""
import sys

import uvicorn

from app import allocator

allocator.jitter_minutes = lambda job_id: 0  # allocate() looks this up at call time, so the patch takes effect

if __name__ == "__main__":
    uvicorn.run("app.main:app", port=int(sys.argv[1]))
```

Create `e2e/stubcloud.py`:

```python
"""A stand-in cloud for the fail-open test in fast mode. The real cloud cannot hand out a start time a few minutes away (its
tariff only flips on the hour), so this speaks the same /agent/v1/sync contract and hands out `now + delay_s`. Like the real
cloud it re-sends a decision until the agent reports it applied. Used only for checks whose names say "stand-in cloud"."""
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8101


class StubCloud:
    def __init__(self, key: str, delay_s: int = 150, port: int = PORT):
        self.key, self.delay_s, self.port = key, delay_s, port
        self.targets: set[str] = set()  # refs that get a decision
        self.decided: dict[str, int] = {}  # ref -> the start time (epoch) handed out, fixed at first sight
        self.applied: dict[str, bool] = {}  # ref -> whether the agent confirmed it
        self.bodies: list[dict] = []
        self._srv = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def handle(self, body: dict) -> dict:
        self.bodies.append(body)
        for a in body.get("applied", []):
            self.applied[a["ref"]] = bool(a["ok"])
        out = []
        for j in body.get("jobs", []):
            ref = j["ref"]
            if j["state"] != "PENDING" or ref not in self.targets or ref in self.applied:
                continue
            start = self.decided.setdefault(ref, int(time.time()) + self.delay_s)
            out.append({"ref": ref, "start_at": datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        return {"decisions": out, "release_all": False, "next_poll_s": 10}

    def start(self) -> None:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, data):
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._send(200, {"ok": True})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))  # always read the body first, or the client sees a reset
                if self.headers.get("X-Site-Key") != stub.key:
                    return self._send(401, {"detail": "missing or invalid X-Site-Key"})
                self._send(200, stub.handle(json.loads(raw)))

            def log_message(self, *args):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self._srv.server_port  # the real port when 0 was asked for
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
```

- [ ] **Step 4: Write `agentproc.py`**

Create `e2e/agentproc.py`:

```python
"""Run the real agent (from its own venv) against the Docker Slurm and the isolated cloud."""
import json
import subprocess
from pathlib import Path

import yaml

import cloud

ROOT = cloud.ROOT
RUN = cloud.RUN
AGENT_PY = ROOT / "agent" / ".venv" / "Scripts" / "python.exe"
# The agent reaches Slurm as the Operator account inside the controller container, with UTC and epoch times.
PREFIX = ["docker", "exec", "-u", "wsagent", "-e", "TZ=UTC", "-e", "SLURM_TIME_FORMAT=%s", "slurmctld"]


def write_config(mode: str, url: str = cloud.BASE, state: str = "state.db") -> Path:
    cfg = {
        "cloud": {"url": url, "key_file": str(RUN / "site.key")}, "mode": mode, "poll_seconds": 10,
        "state_file": str(RUN / state), "audit_file": str(RUN / "audit.log"), "slurm": {"prefix": PREFIX},
        "rules": [{"match": {"qos": "flex"}, "max_wait": "3h"}], "default": "none",
    }
    path = RUN / f"agent-{mode}-{Path(state).stem}.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def cli(mode: str, *args, url: str = cloud.BASE, state: str = "state.db", timeout=120) -> subprocess.CompletedProcess:
    return subprocess.run([str(AGENT_PY), "-m", "wattshift_agent", "-c", str(write_config(mode, url, state)), *args],
                          capture_output=True, text=True, timeout=timeout, cwd=RUN)


def start(mode: str, url: str = cloud.BASE, state: str = "state.db") -> subprocess.Popen:
    log = open(RUN / "agent.log", "ab")
    return subprocess.Popen([str(AGENT_PY), "-m", "wattshift_agent", "-c", str(write_config(mode, url, state)), "run"],
                            stdout=log, stderr=log, cwd=RUN)


def stop(p: subprocess.Popen) -> None:
    p.kill()
    p.wait(timeout=20)


def audit_commands() -> list[list[str]]:
    """Every Slurm command the agent has run, from its own audit log."""
    out = []
    path = RUN / "audit.log"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line.split(" ", 1)[1])
            except (IndexError, ValueError):
                continue
            if "cmd" in rec:
                out.append(rec["cmd"])
    return out
```

- [ ] **Step 5: Run the helper tests**

Run: `.\backend\.venv\Scripts\python -m pytest e2e/test_helpers.py -q`
Expected: all pass. (`test_the_rules_load_through_the_clouds_own_tariff_code` proves the synthetic tariff is accepted by the real cloud code.)

- [ ] **Step 6: Check the cloud starts, seeds and answers on the test database**

Run:

```powershell
cd C:\Users\aakur\OneDrive\Desktop\WattShift
.\backend\.venv\Scripts\python -c "
import sys; sys.path[:0] = ['e2e', 'backend']
from datetime import datetime; import cloud
now = datetime.now(cloud.IST); b = cloud.choose_boundary(now)
key = cloud.seed(now, cloud.peak_hours(now, b)); print('boundary', b, 'key', key[:8] + '...')
p = cloud.start(); print('up:', cloud.is_up(), cloud.api('GET', '/health')); cloud.stop(p); print('up after stop:', cloud.is_up())"
```

Expected: a boundary time, `up: True {'ok': True}`, `up after stop: False`. This also confirms `wattshift_tests` is safe to drop and recreate (the backend tests do the same at the start of each session).

---

### Task 4: The scenario runner, shadow phase first

**Files:**
- Create: `e2e/run.py`

**Interfaces:**
- Consumes: `lab`, `cloud`, `agentproc` (Tasks 1 and 3).
- Produces: `python e2e/run.py [--mode {fast,full}] [--only {preflight,shadow,all}] [--dry-run] [--now]`, which seeds the isolated cloud, runs the chosen phases, prints `PASS`/`FAIL` per check, and writes `docs/superpowers/e2e/<date>-end-to-end-report.md` plus raw outputs in `docs/superpowers/e2e/raw/`. `--dry-run` prints the schedule and stops; `--now` makes full mode start immediately instead of idling until 13 minutes before the hour.

- [ ] **Step 1: Write `run.py`** (all phases; Task 4 runs only preflight and shadow, Task 5 runs everything)

Create `e2e/run.py`:

```python
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
    (cloud.RUN / "site.key").write_text(key)
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
```

- [ ] **Step 2: Run the shadow phase alone**

Run: `.\backend\.venv\Scripts\python e2e\run.py --mode fast --only shadow`
Expected: about 4 minutes (the blockers hold the GPUs for 80 seconds, then the job runs). Every line `PASS`: the lab check, `wattshift-agent check`, and the four shadow checks. Open the report it wrote. If anything fails: read `e2e/.run/agent.log`, `e2e/.run/cloud.log`, and `e2e/.run/audit.log`; decide agent, cloud or harness; fix the real cause. Typical harness problems to expect on a first run: a `docker exec` quoting problem (fix in `lab.py`), a UUID vs string mismatch in `cloud.site_row()` (wrap with `uuid.UUID`), the agent's state file path. Do not proceed to Task 5 until this phase is green.

---

### Task 5: The full run, the report, and what it finds

**Files:**
- Modify (only as findings require): anything the run shows to be wrong.
- Create (generated): `docs/superpowers/e2e/<date>-end-to-end-report.md` and `docs/superpowers/e2e/raw/*.txt`

- [ ] **Step 1: Run fast mode (any time of day, about 17 minutes)**

Tell the user in one message: about 17 minutes, the machine must stay awake and Docker running. Then, in the background, reading its output as it goes (one `PASS`/`FAIL` line per check and a line per phase):

```powershell
cd C:\Users\aakur\OneDrive\Desktop\WattShift
.\backend\.venv\Scripts\python e2e\run.py --mode fast
```

- [ ] **Step 2: Read the report**

Open `docs/superpowers/e2e/<date>-end-to-end-fast-report.md`. Every criterion 1 to 5 and F row should be `PASS`. Note the measured start gaps for `F1` and `F2` (Spike 0 saw +6 s, +20 s and +31 s at default scheduler settings; anything from 0 to about 90 s is consistent; a negative number would mean Slurm started a job early and is a serious finding), and confirm the report's notes say the real cloud's jobs were cancelled while held and which checks used the stand-in cloud.

- [ ] **Step 3: For every FAIL, find the real cause and fix it test-first**

For each failed check, in this order: (a) read the raw outputs and logs for that phase; (b) classify the cause as agent, cloud, or harness; (c) for an agent or cloud cause, write a failing unit test that reproduces it in the right package (`agent/tests/`, `backend/tests/`), fix the code, and make the whole suite green; for a harness cause, fix the harness; (d) re-run the affected phase (`--only shadow` for shadow problems; otherwise the fast run again, and the full run once more if the problem was in the real decision-to-start chain). Never weaken an assertion or widen `LAG` to hide a real problem; if a threshold really is wrong (for example `LAG` below the measured scheduler lag with a justified reason), say so in the report and change it in one place with the evidence next to it.

- [ ] **Step 4: Confirm the suites and the isolation**

Run:

```powershell
cd agent; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider
cd ..\backend; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider
cd ..; .\backend\.venv\Scripts\python -m pytest e2e/test_helpers.py -q
```

Expected: all green. The backend suite drops and recreates the test database at its start, which is fine: the proof re-seeds its own data every run. Confirm the dev database was not touched: `wattshift` still has the earlier "Demo/Lab" site and no `E2E` company.

- [ ] **Step 5: Run full mode once, at a convenient time**

Full mode carries the real cloud's own decision all the way to a real start with the agent and the cloud stopped, which fast mode cannot (the real cloud's tariff only flips on the hour). First see the schedule:

```powershell
.\backend\.venv\Scripts\python e2e\run.py --mode full --dry-run
```

It prints when the run would start (13 minutes before the next top-of-hour) and when it would finish (about 5 minutes after it). Tell the user; if the wait is long, offer to start later (launching around :45 means about 20 minutes in total). Then run `.\backend\.venv\Scripts\python e2e\run.py --mode full` in the background (it idles until its start time, then works for about 17 minutes) and read `docs/superpowers/e2e/<date>-end-to-end-full-report.md`: the `A1`, `A4` and `A5` start gaps and the reconcile checks are the new evidence. Apply Step 3 to any failure.

---

### Task 6: Wrap-up

**Files:**
- Modify: `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md`, `docs/superpowers/spikes/2026-09-19-slurm-spike-findings.md` (only its "Limits" or a short pointer), `C:\Users\aakur\.claude\plans\analyse-all-the-files-snuggly-haven.md`

- [ ] **Step 1: Update the spec**

Section 15: mark step 5 "**End-to-end proof: DONE <date>** (plan `docs/superpowers/plans/2026-09-20-end-to-end-proof.md`, report `docs/superpowers/e2e/<file>`)" with one sentence on the result. Section 16: in "Still to verify", remove the three items the run confirmed (`squeue -o` fields, `sacct -S now-14days`, `TresPerJob`) or replace them with what the run actually found; keep typed GPUs, real GPU hardware, tuned scheduler settings, priority weights, preemption, older Slurm versions. Section 17: next to each criterion 1 to 5 add "verified on real Slurm <date>"; criterion 6 stays open until the measurement plan.

- [ ] **Step 2: Progress note**

Append to the project plan file under "AUTOMATION ARC": the end-to-end proof result (criteria passed, measured start gaps, anything found and fixed), how to re-run it (`python e2e\run.py` for fast mode, about 17 minutes; `--mode full` for the real cloud's decision carried to a real start, which waits for a top-of-hour; both need Docker), and what is next (the measurement and dashboard plan, then Phase 8 hosting).

- [ ] **Step 3: Tear down (with the user's say-so for images)**

Run `.\backend\.venv\Scripts\python e2e\lab.py down` (removes the containers and volumes; the images stay). Then **ask the user** whether to also remove the images (`slurm-docker-cluster:26.05.2`, `giovtorres/slurm-docker-cluster:latest`, `mariadb:12`, about 2.8 GB): keeping them makes a re-run take minutes instead of a re-download.

- [ ] **Step 4: Report**

Tell the user, in plain words: which criteria passed on a real Slurm; the measured start accuracy with the agent and the cloud stopped; every mismatch the run found and how it was fixed (especially any parser change); what is still not covered (dashboard, typed GPUs, real GPU hardware, a different Slurm version); and that the next plan is measurement and the dashboard. Do not commit.

---

## Self-review

**Spec coverage.** Section 14 item 4's cases: shadow changes nothing (Shadow phase), autonomous sets start times inside the cheapest window and Slurm starts the jobs (Release setup, Main), kill the agent and the cloud and the jobs still start on time (Main: a stand-in cloud hands out the start time in fast mode; the real cloud's own decision in full mode), `release_all` clears start times (Release), a user's own change is respected (Main), "the savings row matches a hand calculation" (**deferred**: the savings computation is the measurement plan; this run checks that planned cost is below baseline cost and that the recorded start and end equal Slurm's accounting, which is the data that plan will use). Section 17 criteria 1 to 5 are each a named check; 6 is explicitly out of scope. The three unverified Slurm assumptions from the agent plan are Task 2.

**Placeholders.** None. Two spots tell the executor exactly what to do if a real output differs from an assumption (Task 2 Step 4, Task 5 Step 3) instead of pretending the outcome is known.

**Consistency.** `lab.sbatch`, `lab.show`, `lab.acct`, `lab.until`, `lab.wait_finished`, `lab.state_of` are defined in Task 1 and used with those signatures in Tasks 2 and 4. `cloud.seed`, `cloud.start`, `cloud.stop`, `cloud.managed`, `cloud.managed_prefix`, `cloud.decisions`, `cloud.site_row`, `cloud.api` (Task 3) match `run.py`. `agentproc.start/stop/cli/audit_commands` match. The agent's `QUEUE_FORMAT` and `ACCT_FIELDS` are asserted equal to the capture script's copies.

**Known limits, stated once.** One Slurm version (26.05.2), one config, untyped fake GPUs, a synthetic tariff and prices (so this proves mechanics, not savings), the per-job start-time spread switched off in the proof's cloud, and, in fast mode, a stand-in cloud for the fail-open timing test (the real cloud's own decision reaching a real start is proven only by full mode, which has to wait for a top-of-hour).
