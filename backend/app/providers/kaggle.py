"""Runs jobs as private GPU kernels on Kaggle via the official CLI (free GPU, no card, phone-verified account).
One kernel per job (slug from the job id) so concurrent jobs can never collide on a single kernel."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.models import Job
from app.providers.base import Status

TEMPLATE = Path(__file__).resolve().parents[2] / "kaggle_kernel" / "wattshift_job.py"
POWER_MARK = "WATTSHIFT_POWER "  # the kernel prints one line: this marker, then a JSON summary of its power samples
STATUS = re.compile(r'KernelWorkerStatus\.([A-Z_]+)')
RUNNING = {"QUEUED", "RUNNING", "NEW_SCRIPT"}
FAILED = {"ERROR", "CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED"}


def parse_status(output: str) -> Status:
    m = STATUS.search(output)
    state = m.group(1) if m else None
    if state == "COMPLETE":
        return "done"
    if state in RUNNING:
        return "running"
    if state in FAILED:
        return "failed"
    # Unknown/garbled (e.g. "Not found" right after a push): raise so the poller retries instead of guessing.
    raise ValueError(f"unrecognised kernel status: {output.strip()[:120]!r}")


def parse_power(log: str) -> dict | None:
    """The power summary a kernel printed, or None when there is no usable reading (no line, bad JSON, no samples)."""
    for line in log.splitlines():
        if not line.startswith(POWER_MARK):
            continue
        try:
            d = json.loads(line[len(POWER_MARK):])
            if d["samples"] > 0:
                return {"avg_watts": d["avg_watts"], "peak_watts": d["peak_watts"], "energy_wh": d["energy_wh"], "samples": d["samples"],
                        "gpu_model": d["gpu"], "power_limit_w": d["power_limit_w"]}
        except (ValueError, KeyError, TypeError):
            continue
    return None


def _find_kaggle() -> str:
    exe = shutil.which("kaggle")
    if exe:
        return exe
    sibling = Path(sys.executable).parent / ("kaggle.exe" if os.name == "nt" else "kaggle")
    if sibling.exists():
        return str(sibling)
    raise FileNotFoundError("kaggle CLI not found; pip install kaggle")


class KaggleProvider:
    def __init__(self, username: str, run_seconds: int = 30, timeout_seconds: int = 600, exe: str | None = None):
        if not username:
            raise ValueError("KAGGLE_USERNAME is required")
        self.username, self.run_seconds, self.timeout_seconds, self.exe = username, run_seconds, timeout_seconds, exe

    def _run(self, *args: str) -> str:
        r = subprocess.run([self.exe or _find_kaggle(), *args], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"kaggle {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout

    def start(self, job: Job, tag: str = "") -> str:
        slug = f"wattshift-{job.id.hex[:10]}" + (f"-{tag}" if tag else "")
        ref = f"{self.username}/{slug}"
        script = TEMPLATE.read_text().replace("__JOB_ID__", str(job.id)).replace("__RUN_SECONDS__", str(self.run_seconds))
        meta = {
            "id": ref, "title": slug, "code_file": "job.py", "language": "python", "kernel_type": "script",
            "is_private": "true", "enable_gpu": "true", "enable_internet": "false",
            "dataset_sources": [], "competition_sources": [], "kernel_sources": [],
        }
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "job.py").write_text(script)
            (Path(d) / "kernel-metadata.json").write_text(json.dumps(meta))
            self._run("kernels", "push", "-p", d, "-t", str(self.timeout_seconds))
        return ref

    def status(self, ref: str) -> Status:
        try:
            out = self._run("kernels", "status", ref)
        except RuntimeError as e:
            if "404" in str(e) or "not found" in str(e).lower():
                raise LookupError(f"kernel {ref} not found") from e  # lets the dispatcher give up after a grace period
            raise
        return parse_status(out)

    def measure(self, ref: str) -> dict | None:
        """Download the finished kernel's log and read the power summary out of it."""
        with tempfile.TemporaryDirectory() as d:
            self._run("kernels", "output", ref, "-p", d)
            text = "".join(
                chunk.get("data", "") for f in sorted(Path(d).glob("*.log")) for chunk in json.loads(f.read_text(encoding="utf-8"))
            )
        return parse_power(text)
