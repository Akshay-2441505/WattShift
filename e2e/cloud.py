"""The cloud side of the end-to-end proof: an isolated API + database, and a synthetic tariff and prices."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import Session

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BACKEND = ROOT / "backend"
# Outside the OneDrive-synced project folder: the run directory holds the demo site key, and a synced credential is a leaked one.
RUN = Path.home() / ".wattshift" / "e2e-run"
PORT = 8100
BASE = f"http://127.0.0.1:{PORT}"
IST = timezone(timedelta(hours=5, minutes=30))
STEP = timedelta(minutes=15)
PRICE = 5000.0  # Rs/MWh, constant: with equal prices the tariff zone alone decides where a job goes


def db_url() -> str:
    url = dotenv_values(Path.home() / ".wattshift" / ".env").get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("TEST_DATABASE_URL is missing from ~/.wattshift/.env")
    return url


# --- the synthetic tariff and the timeline -----------------------------------------------------------------------------
def choose_boundary(now: datetime, min_lead: timedelta = timedelta(minutes=13)) -> datetime:
    """The next top-of-hour (IST) that leaves at least min_lead to run the earlier phases; otherwise the one after."""
    b = now.astimezone(IST).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return b if b - now >= min_lead else b + timedelta(hours=1)


def peak_hours(now: datetime, boundary: datetime) -> set[int]:
    """IST hours from the hour containing `now` up to (not including) the boundary: expensive now, cheap after."""
    t = now.astimezone(IST).replace(minute=0, second=0, microsecond=0)
    hours: set[int] = set()
    while t < boundary:
        hours.add(t.hour)
        t += timedelta(hours=1)
    return hours


def hour_rules(peak: set[int]) -> list[dict]:
    """A tariff with 'peak' (+25%) in the given IST hours and 'solar' (-15%) in every other hour, all year."""
    rules: list[dict] = []
    for h in range(24):
        zone, pct = ("peak", 25) if h in peak else ("solar", -15)
        if rules and rules[-1]["zone"] == zone and rules[-1]["end_hour"] == h:
            rules[-1]["end_hour"] = h + 1
        else:
            rules.append({"zone": zone, "start_hour": h, "end_hour": h + 1, "season": None, "adj_pct": pct})
    return rules


# --- seeding, running and reading the isolated cloud ------------------------------------------------------------
def seed(now: datetime, peak: set[int], step: timedelta = STEP, hours: int = 76) -> str:
    """Recreate the TEST database with one E2E site (shadow mode) and return the site key. Test database only."""
    from app import db, sites
    from app.models import PriceSignal, TariffCatalogue

    RUN.mkdir(parents=True, exist_ok=True)
    engine = db.make_engine(db_url())
    with engine.begin() as c:
        name = c.exec_driver_sql("select current_database()").scalar()
        assert "test" in name, f"refusing to seed database {name!r}"
    db.Base.metadata.drop_all(engine)
    db.Base.metadata.create_all(engine)
    today = now.astimezone(IST).date()
    with Session(engine) as s, s.begin():
        s.add(TariffCatalogue(
            utility="TEST", category="E2E", valid_from=today - timedelta(days=1), valid_until=today + timedelta(days=365),
            rules=hour_rules(peak), base_rate=8.44, verified=False, source="end-to-end proof: synthetic tariff",
        ))
        if step >= timedelta(minutes=15):
            t = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        else:
            t = now.astimezone(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=10)  # the time-lapse demo
        end = t + timedelta(hours=hours)
        while t < end:
            s.add(PriceSignal(ts=t, price_rs_per_mwh=PRICE, source="iex_dam"))
            t += step
        site, key = sites.create_site(s, "E2E", "Lab", gpus=8, shift_capacity_share=1.0, utility="TEST", tariff_category="E2E", mode="shadow")
        (RUN / "site_id.txt").write_text(str(site.id))
    return key


def site_id() -> str:
    return (RUN / "site_id.txt").read_text().strip()


def is_up() -> bool:
    try:
        urllib.request.urlopen(BASE + "/health", timeout=2)
        return True
    except OSError:
        return False


def start(launcher: str = "cloud_main.py") -> subprocess.Popen:
    RUN.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("SCHEDULER", "DEMO_MODE", "API_KEY")}
    env.update(DATABASE_URL=db_url(), PYTHONPATH=str(BACKEND))
    log = open(RUN / "cloud.log", "ab")
    p = subprocess.Popen([sys.executable, str(HERE / launcher), str(PORT)], cwd=BACKEND, env=env, stdout=log, stderr=log)
    for _ in range(60):
        if is_up():
            return p
        time.sleep(1)
    p.kill()
    raise RuntimeError("the cloud did not start; see ~/.wattshift/e2e-run/cloud.log")


def stop(p: subprocess.Popen) -> None:
    p.kill()
    p.wait(timeout=20)


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


@contextmanager
def session():
    from app import db

    with Session(db.make_engine(db_url())) as s:
        yield s


def _row(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def managed(ref: str) -> dict | None:
    from app.models import ManagedJob

    with session() as s:
        row = s.scalar(select(ManagedJob).where(ManagedJob.ref == ref))
        return _row(row) if row else None


def managed_prefix(prefix: str) -> dict | None:
    """The managed job whose ref starts with `prefix` (a pending array's ref is like 41_[1-3])."""
    from app.models import ManagedJob

    with session() as s:
        row = s.scalar(select(ManagedJob).where(ManagedJob.ref.like(prefix + "%")))
        return _row(row) if row else None


def decisions(ref: str) -> list[dict]:
    from app.models import Decision, ManagedJob

    with session() as s:
        rows = s.scalars(select(Decision).join(ManagedJob, ManagedJob.id == Decision.managed_job_id).where(ManagedJob.ref == ref).order_by(Decision.id))
        return [_row(r) for r in rows]


def site_row() -> dict:
    from app.models import Site

    with session() as s:
        return _row(s.get(Site, site_id()))


def audit_events() -> list[tuple]:
    from app.models import AuditLog

    with session() as s:
        return [(a.actor, a.event, a.ref) for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]


def set_peak(peak: set[int]) -> None:
    """Rewrite the E2E tariff's expensive hours in place (the demo re-aims the cheap window when the presenter starts)."""
    from app.models import TariffCatalogue

    with session() as s:
        row = s.scalar(select(TariffCatalogue).where(TariffCatalogue.utility == "TEST", TariffCatalogue.category == "E2E"))
        row.rules = hour_rules(peak)
        s.commit()


def write_site_key(key: str) -> None:
    """Save the demo site key where the agent will read it, readable by this user only (the agent refuses looser files)."""
    RUN.mkdir(parents=True, exist_ok=True)
    path = RUN / "site.key"
    path.write_text(key)
    path.chmod(0o600)
