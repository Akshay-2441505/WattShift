"""Replay mode: a fast simulated clock plus a REAL historical IEX day mapped onto the simulated days, so a demo that
would take hours finishes in minutes. Prices are stored as source='iex_dam_replay' and always labeled as replay."""
import csv
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import Job, JobRun, PriceSignal, SavingsLog
from app.tariff import IST

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "iex_sample_day.csv"
SOURCE = "iex_dam_replay"


def load_sample_day() -> list[tuple[int, int, float]]:
    """[(hour, minute, mcp_rs_per_mwh)] for the 96 blocks of the captured real IEX day."""
    with open(SAMPLE, newline="") as f:
        out = []
        for row in csv.DictReader(f):
            t = datetime.fromisoformat(row["block_start_ist"])
            out.append((t.hour, t.minute, float(row["mcp_rs_per_mwh"])))
    return sorted(out)


def seed_replay(session: Session, start: str = "19:00", today: date | None = None, reset_jobs: bool = True) -> datetime:
    """Load the sample day for sim-today and sim-tomorrow (same time of day, so ToD zones and the solar dip line up),
    optionally clear jobs + savings, and return the sim-clock anchor. The caller commits, then starts the clock."""
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", start or "")
    if not m:
        raise ValueError("start must be HH:MM (24h, IST)")
    today = today or datetime.now(IST).date()
    anchor = datetime.combine(today, time(int(m[1]), int(m[2])), tzinfo=IST)

    session.execute(delete(PriceSignal).where(PriceSignal.source == SOURCE))
    sample = load_sample_day()
    session.add_all(
        PriceSignal(ts=datetime.combine(today + timedelta(days=d), time(h, mi), tzinfo=IST), price_rs_per_mwh=p, source=SOURCE)
        for d in (0, 1)
        for h, mi, p in sample
    )
    if reset_jobs:
        session.execute(delete(SavingsLog))
        session.execute(delete(JobRun))
        session.execute(delete(Job))
    session.flush()
    return anchor
