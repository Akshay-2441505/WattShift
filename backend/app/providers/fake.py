import time

from app.models import Job
from app.providers.base import Status


class FakeProvider:
    """Test double: records calls, lets tests script statuses and failures."""

    def __init__(self, start_delay: float = 0.0, fail_start: bool = False):
        self.started: list[str] = []
        self.statuses: dict[str, Status | Exception] = {}
        self.measurements: dict[str, dict | None | Exception] = {}  # unset refs measure as a steady 68 W T4
        self.start_delay = start_delay
        self.fail_start = fail_start

    def start(self, job: Job, tag: str = "") -> str:
        time.sleep(self.start_delay)
        if self.fail_start:
            raise RuntimeError("kaggle exploded")
        ref = f"fake/{job.id}" + (f"-{tag}" if tag else "")
        self.started.append(ref)
        return ref

    def status(self, ref: str) -> Status:
        s = self.statuses.get(ref, "running")
        if isinstance(s, Exception):
            raise s
        return s

    def measure(self, ref: str) -> dict | None:
        m = self.measurements.get(ref, {"avg_watts": 68.0, "peak_watts": 70.0, "energy_wh": 0.57, "samples": 30, "gpu_model": "Tesla T4", "power_limit_w": 70.0})
        if isinstance(m, Exception):
            raise m
        return m
