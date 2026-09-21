"""The single source of 'now'. Live mode = real time; replay/demo mode = simulated time running `scale`x faster."""
from datetime import datetime, timezone

_sim: tuple[datetime, datetime, float] | None = None  # (real anchor, sim anchor, scale)


def now() -> datetime:
    real = datetime.now(timezone.utc)
    if _sim is None:
        return real
    real0, sim0, scale = _sim
    return sim0 + (real - real0) * scale


def start_sim(anchor: datetime, scale: float) -> None:
    global _sim
    _sim = (datetime.now(timezone.utc), anchor, scale)


def reset() -> None:
    global _sim
    _sim = None


def is_sim() -> bool:
    return _sim is not None


def scale() -> float:
    return _sim[2] if _sim else 1.0
