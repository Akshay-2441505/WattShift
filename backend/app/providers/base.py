from typing import Literal, Protocol

from app.models import Job

Status = Literal["running", "done", "failed"]


class ComputeProvider(Protocol):
    """The only thing the scheduler knows about where jobs run. Implementations must not touch the DB."""

    def start(self, job: Job, tag: str = "") -> str:
        """Start the job now; return a handle used by status(). Raise on failure. `tag` makes a second run of the same
        job (the 'without' twin) a separate run."""

    def status(self, ref: str) -> Status: ...

    def measure(self, ref: str) -> dict | None:
        """The power a finished run was measured drawing (see parse_power), or None if it cannot be read."""
