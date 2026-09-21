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
