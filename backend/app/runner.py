"""APScheduler wiring. Interval tasks only; the DB is the source of truth (no per-job triggers)."""
import logging
from datetime import datetime
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from app import clock, dispatch
from app.config import settings
from app.ingest_iex import ingest
from app.providers.base import ComputeProvider
from app.service import replan_pending

log = logging.getLogger("wattshift.runner")


def build_providers(settings) -> dict[str, ComputeProvider]:
    """Only configured providers are registered; jobs for anything else fail loudly with 'not configured'.
    # ponytail: GitHub Actions fallback not built; add github_actions.py when Kaggle proves unreliable."""
    from app.providers.kaggle import KaggleProvider

    providers: dict[str, ComputeProvider] = {}
    if settings.kaggle_username:
        providers["kaggle"] = KaggleProvider(
            settings.kaggle_username, settings.kaggle_run_seconds, settings.kaggle_timeout_seconds
        )
    return providers


def tick(factory, providers: dict[str, ComputeProvider], now: Callable[[], datetime]) -> None:
    with factory() as s:
        dispatch.fire_due_jobs(s, providers, now())
        dispatch.fire_twins(s, providers, now())  # after the real jobs: they get the free slots first
        dispatch.poll_running(s, providers)
        dispatch.poll_twins(s, providers)


def replan_tick(factory, now: Callable[[], datetime] = clock.now) -> None:
    with factory() as s, s.begin():
        stats = replan_pending(s, now=now())
    if stats["placed"] or stats["moved"]:
        log.info("replan: %s", stats)


def ingest_prices(factory, now: Callable[[], datetime] = clock.now) -> None:
    with factory() as s, s.begin():
        ingest(s)
    replan_tick(factory, now)  # new prices are the main reason to re-plan: do it straight away


def start(
    factory,
    providers: dict[str, ComputeProvider],
    dispatch_seconds: float = 5,
    ingest: bool = True,
    now: Callable[[], datetime] = clock.now,
    replan_seconds: float | None = None,
) -> BackgroundScheduler:
    with factory() as s:
        n = dispatch.recover_on_startup(s)
    if n:
        log.warning("recovered %d job(s) interrupted before provider start", n)
    sched = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1})
    dispatch_every = dispatch_seconds if providers else max(dispatch_seconds, settings.idle_dispatch_seconds)
    sched.add_job(tick, "interval", seconds=dispatch_every, args=[factory, providers, now], id="dispatch")
    sched.add_job(
        replan_tick, "interval", seconds=replan_seconds or settings.replan_seconds, args=[factory, now], id="replan"
    )
    if ingest:
        # IEX publishes tomorrow's prices around midday; ingest is idempotent, so poll every 3h and once at boot.
        sched.add_job(ingest_prices, "interval", hours=3, args=[factory, now], id="ingest", next_run_time=datetime.now())
    sched.start()
    return sched
