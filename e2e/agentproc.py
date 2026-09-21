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
