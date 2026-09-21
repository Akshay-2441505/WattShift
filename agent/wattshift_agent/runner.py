"""The only place the agent touches Slurm: an allow-list of commands, a fixed environment, one audit line each."""
import json
import os
import re
import subprocess
import time

JOB_ID = re.compile(r"\d+")
ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class ForbiddenCommand(Exception):
    """The agent tried to run something that is not on its allow-list. Always a bug; nothing was executed."""


class SlurmError(Exception):
    """A Slurm command failed (non-zero exit, timeout, or the binary is missing)."""


def check_command(argv, allow_defer: bool) -> None:
    """Raise ForbiddenCommand unless argv is one of the few commands the agent may run.
    Reads: squeue, sacct, `scontrol show job <id>`. Writes: only `scontrol update JobId=<id> StartTime=<value>`, where
    `now` (a release) is always allowed, so a shadow agent can undo its own earlier deferrals, and an absolute UTC time
    (a deferral) only when allow_defer."""
    if not argv:
        raise ForbiddenCommand("empty command")
    cmd, rest = argv[0], argv[1:]
    if cmd in ("squeue", "sacct"):
        return
    if cmd == "scontrol" and len(rest) == 3 and rest[:2] == ["show", "job"] and JOB_ID.fullmatch(rest[2]):
        return
    if (
        cmd == "scontrol" and len(rest) == 3 and rest[0] == "update" and rest[1].startswith("JobId=")
        and JOB_ID.fullmatch(rest[1][len("JobId="):]) and rest[2].startswith("StartTime=")
    ):
        value = rest[2][len("StartTime="):]
        if value == "now":
            return
        if ISO_UTC.fullmatch(value):
            if allow_defer:
                return
            raise ForbiddenCommand("setting a future start time is not allowed in shadow mode")
    raise ForbiddenCommand(f"command not on the allow-list: {' '.join(argv)[:80]}")


class SubprocessRunner:
    def __init__(self, *, allow_defer: bool, prefix=(), audit=None, timeout: int = 30):
        self.allow_defer, self.prefix, self.audit, self.timeout = allow_defer, list(prefix), audit, timeout

    def run(self, argv) -> str:
        check_command(argv, self.allow_defer)
        env = {**os.environ, "TZ": "UTC", "SLURM_TIME_FORMAT": "%s"}  # UTC in, epoch seconds out (Spike 0)
        t0 = time.monotonic()
        try:
            p = subprocess.run([*self.prefix, *argv], capture_output=True, text=True, timeout=self.timeout, env=env)
        except FileNotFoundError:
            self._log(argv, None, t0)
            raise SlurmError(f"{(self.prefix or argv)[0]} not found on this machine")
        except subprocess.TimeoutExpired:
            self._log(argv, "timeout", t0)
            raise SlurmError(f"{argv[0]} timed out after {self.timeout} s")
        self._log(argv, p.returncode, t0)
        if p.returncode != 0:
            raise SlurmError((p.stderr or p.stdout).strip()[:300] or f"{argv[0]} exited with {p.returncode}")
        return p.stdout

    def _log(self, argv, rc, t0) -> None:
        if self.audit is not None:
            self.audit.info(json.dumps({"cmd": list(argv), "rc": rc, "ms": int((time.monotonic() - t0) * 1000)}))
