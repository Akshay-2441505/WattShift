"""Pure parsers for the text Slurm prints. Times are epoch seconds because the agent sets SLURM_TIME_FORMAT=%s."""
import re
from dataclasses import dataclass


class ParseError(ValueError):
    """Slurm printed something the agent does not understand."""


TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OTHER")
_RUNNING = ("RUNNING", "COMPLETING", "CONFIGURING", "SUSPENDED", "RESIZING", "SIGNALING", "STAGE_OUT")
_KV = re.compile(r"(\S+?)=(\S*)")
_GPU = re.compile(r"gres/gpu(?![\w/])(?::[A-Za-z][\w.\-]*)?(?::(\d+))?")  # gres/gpu, gres/gpu:3, gres/gpu:a100:4
_GPU_TOTAL = re.compile(r"gres/gpu=(\d+)")
_LIMIT = re.compile(r"(?:(\d+)-)?(\d+):(\d+):(\d+)")


def epoch(text) -> int | None:
    t = (text or "").strip()
    return int(t) if t.isdigit() else None


def _int(text, default=0) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def normalize_state(raw: str) -> str:
    s = (raw or "").strip().upper()
    if s.startswith("PENDING"):
        return "PENDING"
    if s in _RUNNING:
        return "RUNNING"
    for full in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"):
        if s.startswith(full):  # "CANCELLED by 2001", "CANCELLED+"
            return full
    return "OTHER"


@dataclass(frozen=True)
class QueueRow:
    ref: str
    state: str
    user: str
    account: str
    qos: str
    partition: str
    submit: int | None
    start: int | None  # Slurm's estimate (or a user's --begin) for a pending job; the real start for a running one
    reason: str
    name: str


def parse_queue(text: str) -> list[QueueRow]:
    """Lines of `%i|%T|%u|%a|%q|%P|%V|%S|%r|%j`; the name is last so a | inside it survives."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        p = line.split("|", 9)
        if len(p) != 10:
            raise ParseError(f"unexpected squeue line: {line[:80]!r}")
        ref, state, user, account, qos, partition, submit, start, reason, name = p
        rows.append(QueueRow(ref, normalize_state(state), user, account, qos, partition, epoch(submit), epoch(start), reason, name))
    return rows


def parse_kv(text: str) -> dict[str, str]:
    """`scontrol show job` prints key=value tokens. The first occurrence of a key wins, so a later SubmitLine or
    Command containing key=value words cannot override the real fields."""
    out: dict[str, str] = {}
    for m in _KV.finditer(text):
        out.setdefault(m.group(1), m.group(2))
    return out


def _gpu_count(text: str) -> int:
    return sum(int(m.group(1) or 1) for m in _GPU.finditer(text))


def gpus_from(kv: dict[str, str]) -> int:
    """GPUs the job asked for. ReqTRES has the total when the cluster tracks gres/gpu in accounting; otherwise the
    live view has it as TresPerJob (total) or TresPerNode (per node)."""
    m = _GPU_TOTAL.search(kv.get("ReqTRES", ""))
    if m:
        return int(m.group(1))
    per_job = _gpu_count(kv.get("TresPerJob", ""))
    if per_job:
        return per_job
    per_node = _gpu_count(kv.get("TresPerNode", ""))
    if per_node:
        first = re.match(r"\d+", kv.get("NumNodes", "1") or "1")  # "2" or a range like "2-4": count the minimum
        return per_node * max(int(first.group(0)) if first else 1, 1)
    return 0


def limit_minutes(text: str) -> int | None:
    """scontrol prints TimeLimit as [D-]HH:MM:SS. UNLIMITED, Partition_Limit or anything else gives None (the cloud
    then leaves the job alone). Rounded up to whole minutes."""
    m = _LIMIT.fullmatch((text or "").strip())
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return max(1, -(-(((d * 24 + h) * 60 + mi) * 60 + s) // 60))


@dataclass(frozen=True)
class JobDetail:
    ref: str
    state: str
    reason: str
    dependency: bool
    restarts: int
    array: bool
    gpus: int
    time_limit_min: int | None
    submit: int | None
    eligible: int | None  # the begin time: what an update of StartTime sets
    start: int | None


def parse_detail(text: str, ref: str) -> JobDetail:
    kv = parse_kv(text)
    if "JobId" not in kv or "JobState" not in kv:
        raise ParseError(f"not a job description for {ref}: {text[:80]!r}")
    return JobDetail(
        ref=ref, state=normalize_state(kv["JobState"]), reason=kv.get("Reason", "None"),
        dependency=kv.get("Dependency", "(null)") not in ("(null)", "", "N/A"),
        restarts=_int(kv.get("Restarts")), array="ArrayJobId" in kv, gpus=gpus_from(kv),
        time_limit_min=limit_minutes(kv.get("TimeLimit", "")), submit=epoch(kv.get("SubmitTime")),
        eligible=epoch(kv.get("EligibleTime")), start=epoch(kv.get("StartTime")),
    )


@dataclass(frozen=True)
class AcctRow:
    ref: str
    state: str
    start: int | None  # None for a job cancelled while pending
    end: int | None
    elapsed_s: int | None
    gpus: int | None  # None unless the cluster tracks gres/gpu in accounting


def parse_acct(text: str) -> list[AcctRow]:
    """Rows of `JobID|State|Start|End|ElapsedRaw|AllocTRES` (sacct -P -X -n)."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        p = line.split("|")
        if len(p) != 6:
            raise ParseError(f"unexpected sacct line: {line[:80]!r}")
        ref, state, start, end, elapsed, alloc = p
        m = _GPU_TOTAL.search(alloc)
        rows.append(AcctRow(ref, normalize_state(state), epoch(start), epoch(end), epoch(elapsed), int(m.group(1)) if m else None))
    return rows
