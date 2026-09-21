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
