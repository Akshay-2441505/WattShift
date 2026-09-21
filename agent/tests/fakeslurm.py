"""An in-process Slurm stand-in. It answers the exact commands the agent sends, in the text formats recorded in
tests/fixtures/slurm (epoch times, pipe-delimited squeue and sacct rows, scontrol key=value blocks), and it applies
the same allow-list as the real runner, so a test also proves the agent only issues allowed commands."""
import calendar
import re
import time

from wattshift_agent.runner import SlurmError, check_command


class FakeSlurm:
    def __init__(self, now: int = 1_790_000_000, allow_defer: bool = True):
        self.now, self.allow_defer = now, allow_defer
        self.jobs: dict[str, dict] = {}
        self.commands: list[list[str]] = []
        self.fail_updates: set[str] = set()  # `scontrol update` on these ids is refused ("Invalid user id")
        self.silent_updates: set[str] = set()  # ...or accepted but changes nothing
        self.accounting = True  # False: sacct knows nothing (accounting has not caught up)
        self.down = False  # True: every command fails, like an unreachable controller

    def add(self, ref, *, state="PENDING", user="alice", account="labs", qos="flex", partition="cpu", name="train",
            gpus=4, limit_min=60, runtime_s=600, predicted=None, dependency=None, restarts=0, nodes=1, gres=None):
        self.jobs[ref] = dict(
            ref=ref, state=state, state_text=state, user=user, account=account, qos=qos, partition=partition, name=name,
            gpus=gpus, limit_min=limit_min, runtime_s=runtime_s, predicted=predicted, dependency=dependency,
            restarts=restarts, nodes=nodes, gres=gres, begin=None, eligible=self.now, submit=self.now, start=None,
            end=None, held=False,
        )
        return self.jobs[ref]

    # --- things a test can do to the "cluster" -------------------------------------------------------------------
    def advance(self, seconds: int) -> None:
        before, self.now = self.now, self.now + seconds
        for j in self.jobs.values():
            if j["state"] == "PENDING" and not j["held"] and not j["dependency"]:
                gate = max(j["begin"] or 0, j["predicted"] or 0)
                if gate <= self.now:
                    j["state"] = j["state_text"] = "RUNNING"
                    j["start"] = max(gate, before)
            if j["state"] == "RUNNING" and j["start"] + j["runtime_s"] <= self.now:
                j["state"] = j["state_text"] = "COMPLETED"
                j["end"] = j["start"] + j["runtime_s"]

    def cancel(self, ref, by=2001):
        j = self.jobs[ref]
        j["state"], j["state_text"], j["end"] = "CANCELLED", f"CANCELLED by {by}", self.now

    def user_set_start(self, ref, epoch):  # the job's owner edits her own job
        self.jobs[ref]["begin"] = self.jobs[ref]["eligible"] = epoch

    # --- the command interface ------------------------------------------------------------------------------------
    def run(self, argv):
        check_command(argv, self.allow_defer)
        self.commands.append(list(argv))
        if self.down:
            raise SlurmError("slurm_load_jobs error: Unable to contact slurm controller")
        if argv[0] == "squeue":
            return self._squeue()
        if argv[0] == "sacct":
            return self._sacct(argv)
        if argv[1] == "show":
            return self._show(argv[3])
        return self._update(argv[2][len("JobId="):], argv[3][len("StartTime="):])

    def _reason(self, j):
        if j["held"]:
            return "JobHeldUser"
        if j["dependency"]:
            return "Dependency"
        if j["begin"] and j["begin"] > self.now:
            return "BeginTime"
        return "None"

    def _shown_start(self, j):
        if j["state"] == "RUNNING":
            return j["start"]
        return max((x for x in (j["begin"], j["predicted"]) if x), default=None)

    def _squeue(self):
        rows = []
        for j in self.jobs.values():
            if j["state"] in ("PENDING", "RUNNING"):
                start = self._shown_start(j)
                rows.append("|".join([
                    j["ref"], j["state"], j["user"], j["account"], j["qos"], j["partition"], str(j["submit"]),
                    str(start) if start else "N/A", self._reason(j), j["name"],
                ]))
        return "\n".join(rows) + ("\n" if rows else "")

    @staticmethod
    def _limit(minutes):
        if minutes is None:
            return "UNLIMITED"
        d, rem = divmod(minutes, 1440)
        h, m = divmod(rem, 60)
        return f"{d}-{h:02d}:{m:02d}:00" if d else f"{h:02d}:{m:02d}:00"

    def _show(self, ref):
        j = self.jobs.get(ref)
        if j is None or j.get("hide_detail"):  # hide_detail: the job vanished between squeue and scontrol
            raise SlurmError("slurm_load_jobs error: Invalid job id specified")
        u = lambda v: "Unknown" if v is None else str(v)  # noqa: E731
        gres = j["gres"] or (f"gres/gpu:{j['gpus']}" if j["gpus"] else None)
        lines = [
            f"JobId={ref} JobName={j['name']}",
            f"   UserId={j['user']}(2001) GroupId={j['user']}(2001) MCS_label=N/A",
            f"   Priority=4294901744 Nice=0 Account={j['account']} QOS={j['qos']}",
            f"   JobState={j['state']} Reason={self._reason(j)} Dependency={j['dependency'] or '(null)'}",
            f"   Requeue=1 Restarts={j['restarts']} BatchFlag=1 Reboot=0 ExitCode=0:0",
            f"   RunTime=00:00:00 TimeLimit={self._limit(j['limit_min'])} TimeMin=N/A",
            f"   SubmitTime={j['submit']} EligibleTime={u(j['eligible'])} AccrueTime={u(j['eligible'])}",
            f"   StartTime={u(self._shown_start(j))} EndTime=Unknown Deadline=N/A",
            f"   Partition={j['partition']} AllocNode:Sid=slurmctld:2210",
            f"   NumNodes={j['nodes']} NumCPUs=1 NumTasks=1 CPUs/Task=1 ReqB:S:C:T=0:0:*:*",
            "   ReqTRES=cpu=1,mem=11815M,node=1,billing=1",
        ]
        if gres:
            lines.append(f"   TresPerNode={gres}")
        lines.append(f"   SubmitLine=sbatch --parsable --gres=gpu:{j['gpus']} -t {j['limit_min']} --wrap=sleep 5")
        return "\n".join(lines) + "\n"

    def _update(self, ref, value):
        j = self.jobs.get(ref)
        if ref in self.fail_updates:
            raise SlurmError(f"Invalid user id for job {ref}")
        if j is None or j["state"] != "PENDING":
            raise SlurmError("Job is no longer pending execution")
        if ref in self.silent_updates:
            return ""
        if value == "now":
            j["begin"], j["eligible"] = None, self.now
        else:
            t = calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%S"))
            j["begin"] = j["eligible"] = t
        return ""

    def _sacct(self, argv):
        if not self.accounting:
            return ""
        rows = []
        refs = argv[argv.index("-j") + 1].split(",")
        for ref in refs:
            if not re.fullmatch(r"\d+(_\d+)?", ref):  # what a real sacct does with `41_[1-3]` (seen on Slurm 26.05.2)
                raise SlurmError(f"sacct: fatal: Bad job/step specified: {ref}: JobID includes unexpected non-numeric characters")
        for ref in refs:
            j = self.jobs.get(ref)
            if j is None:
                continue
            elapsed = (j["end"] - j["start"]) if j["start"] and j["end"] else 0
            rows.append("|".join([
                ref, j["state_text"], str(j["start"]) if j["start"] else "None", str(j["end"]) if j["end"] else "Unknown",
                str(elapsed), "billing=1,cpu=1,mem=11815M,node=1",
            ]))
        return "\n".join(rows) + ("\n" if rows else "")
