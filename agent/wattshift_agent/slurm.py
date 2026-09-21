"""What the agent asks Slurm, in one place. Each method is one allow-listed command plus its parser."""
from datetime import datetime, timezone

from wattshift_agent.parse import AcctRow, JobDetail, ParseError, QueueRow, parse_acct, parse_detail, parse_queue
from wattshift_agent.runner import SlurmError

QUEUE_FORMAT = "%i|%T|%u|%a|%q|%P|%V|%S|%r|%j"
ACCT_FIELDS = "JobID,State,Start,End,ElapsedRaw,AllocTRES"


def fmt_utc(epoch: int) -> str:
    """The absolute-time form scontrol accepts, in UTC (the runner sets TZ=UTC so Slurm reads it as UTC)."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class Slurm:
    def __init__(self, runner):
        self.runner = runner

    def queue(self) -> list[QueueRow]:
        return parse_queue(self.runner.run(["squeue", "-h", "-t", "PENDING,RUNNING", "-o", QUEUE_FORMAT]))

    def detail(self, ref: str) -> JobDetail | None:
        """None when Slurm no longer knows the job (it finished and aged out, or was cancelled)."""
        try:
            return parse_detail(self.runner.run(["scontrol", "show", "job", ref]), ref)
        except SlurmError as e:
            if "Invalid job id" in str(e):
                return None
            raise
        except ParseError:
            return None

    def finished(self, refs: list[str]) -> list[AcctRow]:
        if not refs:
            return []
        out = self.runner.run(["sacct", "-j", ",".join(refs), "-P", "-X", "-n", "-S", "now-14days", "-o", ACCT_FIELDS])
        return parse_acct(out)

    def set_start(self, ref: str, epoch: int) -> None:
        self.runner.run(["scontrol", "update", f"JobId={ref}", f"StartTime={fmt_utc(epoch)}"])

    def release(self, ref: str) -> None:
        self.runner.run(["scontrol", "update", f"JobId={ref}", "StartTime=now"])
