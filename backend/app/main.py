import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent_sync, backtest_runs, clock, db, measured, replay, runner, service, site_views, sites
from app.backtest import BacktestInputError
from app.forecast import build_forecast
from app.config import settings
from app.models import Company, Job, SavingsLog, Site
from app.safety import safe_reason
from app.savings import summary
from app.seed import load_rules
from app.tariff import IST, TodRule, season_of, tod_zone

_factory = None


def factory():
    global _factory
    if _factory is None:
        assert settings.database_url, "DATABASE_URL not set (see ~/.wattshift/.env)"
        _factory = db.make_session_factory(db.make_engine(settings.database_url))
    return _factory


def check_production(cfg) -> None:
    """A hosted service must not accept writes from anyone: PRODUCTION=1 without API_KEY is a startup error, and the
    demo endpoints (which can wipe every job and speed up the clock) must never exist on a hosted service."""
    if cfg.production and not cfg.api_key:
        raise RuntimeError("PRODUCTION=1 requires API_KEY (it guards every write endpoint)")
    if cfg.production and getattr(cfg, "demo_mode", False):
        raise RuntimeError("PRODUCTION=1 cannot run with DEMO_MODE=1 (the demo endpoints can delete every job)")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    check_production(settings)
    if not settings.api_key:
        logging.getLogger("wattshift.api").warning("API_KEY is not set: every write endpoint is open. Fine on your own computer, never on a network.")
    # The scheduler runs in this process only when SCHEDULER=1 (tests and one-off tools leave it off).
    sched = (
        runner.start(factory(), runner.build_providers(settings), dispatch_seconds=settings.dispatch_seconds)
        if settings.run_scheduler
        else None
    )
    yield
    if sched:
        sched.shutdown(wait=False)


# The interactive docs list every endpoint and its fields: useful locally, a free map for a stranger on a hosted API.
_docs = {} if not settings.production else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="Wattshift", lifespan=lifespan, **_docs)
MAX_BODY_BYTES = 6_000_000  # the largest legitimate body is a 5 MB CSV upload plus its JSON wrapper
_HARDENING = {
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store", "Cross-Origin-Resource-Policy": "same-site",
}


@app.middleware("http")
async def harden(request, call_next):
    """Refuse an oversized body before it is read, and stamp every response with the basic browser protections."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        response = JSONResponse(status_code=413, content={"detail": "request body too large"})
    else:
        response = await call_next(request)
    response.headers.update(_HARDENING)
    return response


if settings.cors_origins:  # the deployed dashboard is on another origin; in dev Vite proxies /api, so none is needed
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "X-API-Key"])


@app.get("/", include_in_schema=False)
def home():
    if settings.production:
        return {"service": "wattshift-api", "health": "/health"}
    return RedirectResponse("/docs")  # the API's own page lists every endpoint; the dashboard is a separate site


def get_session():
    with factory()() as s:
        yield s


def get_now() -> datetime:
    return clock.now()


def require_api_key(x_api_key: str | None = Header(None)) -> None:
    # Local dev: no API_KEY configured means writes are open. Deploys must set it (plan Phase 8).
    if settings.api_key and not hmac.compare_digest(x_api_key or "", settings.api_key):
        raise HTTPException(401, "missing or invalid X-API-Key")


class JobIn(BaseModel):
    duration_minutes: int = Field(ge=1, le=service.MAX_DURATION_MIN)
    deadline: AwareDatetime
    provider: Literal["kaggle", "github_actions"]
    power_kw: float | None = Field(None, gt=0, le=1000)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    provider: str
    duration_minutes: int
    deadline: datetime
    power_kw: float
    assigned_window_start: datetime | None
    tod_zone: str | None = None
    baseline_cost_rs: float | None
    planned_cost_rs: float | None
    submitted_at: datetime
    executed_at: datetime | None
    failure_reason: str | None
    actual_cost_rs: float | None = None  # set once the job has finished and its savings are recorded
    saved_rs: float | None = None


def to_out(job: Job, rules: list[TodRule], savings: dict | None = None) -> JobOut:
    out = JobOut.model_validate(job)
    out.failure_reason = safe_reason(job.failure_reason)  # rows written before the scrub existed are cleaned on the way out
    if job.assigned_window_start:
        out.tod_zone = tod_zone(job.assigned_window_start, rules).zone
    if savings and job.id in savings:
        out.actual_cost_rs = float(savings[job.id].actual_cost_rs)
        out.saved_rs = float(savings[job.id].saved_rs)
    return out


def savings_by_job(session: Session) -> dict:
    return {s.job_id: s for s in session.scalars(select(SavingsLog))}


@app.get("/health")
def health():
    # can_run_jobs: this server both has a compute provider and is dispatching to it. A hosted copy has neither (no Kaggle token).
    return {"ok": True, "can_run_jobs": bool(settings.run_scheduler and settings.kaggle_username)}


@app.post("/jobs", status_code=201, dependencies=[Depends(require_api_key)])
def create_job(body: JobIn, session: Session = Depends(get_session), now: datetime = Depends(get_now)):
    try:
        job = service.submit_job(
            session, body.duration_minutes, body.deadline, body.provider, body.power_kw, now=now
        )
    except service.TooManyActiveJobs as e:
        session.rollback()
        raise HTTPException(429, str(e))
    except ValueError as e:
        session.rollback()
        raise HTTPException(422, str(e))
    session.commit()
    out = to_out(job, load_rules(session))
    if job.status == "queued":
        # Stored (never dropped) but nothing fits before the deadline: surface it to the operator.
        return JSONResponse(
            status_code=409,
            content=jsonable_encoder({"detail": "no capacity before the deadline; job queued", "job": out}),
        )
    return out


@app.get("/forecast")
def get_forecast(hours: int = Query(24, ge=1, le=48), session: Session = Depends(get_session), now: datetime = Depends(get_now)):
    return build_forecast(session, now, hours)


def require_demo_mode() -> None:
    if not settings.demo_mode:
        raise HTTPException(404, "not found")  # the demo endpoints simply do not exist outside DEMO_MODE=1


class ReplayIn(BaseModel):
    start: str = Field("19:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # simulated IST start time today
    scale: float = Field(180, ge=1, le=3600)  # simulated seconds per real second
    reset: bool = True  # also delete all jobs and savings


@app.post("/demo/replay", dependencies=[Depends(require_demo_mode), Depends(require_api_key)])
def demo_replay(body: ReplayIn, session: Session = Depends(get_session)):
    """Single-process: the simulated clock is process-global, so run one uvicorn worker in demo mode."""
    try:
        anchor = replay.seed_replay(session, body.start, reset_jobs=body.reset)
    except ValueError as e:
        session.rollback()
        raise HTTPException(422, str(e))
    session.commit()
    clock.start_sim(anchor, body.scale)
    return {"mode": "replay", "sim_start": anchor, "scale": body.scale, "prices_loaded": 192, "jobs_reset": body.reset}


@app.post("/demo/live", dependencies=[Depends(require_demo_mode), Depends(require_api_key)])
def demo_live():
    clock.reset()
    return {"mode": "live"}


class BacktestAssumptionsIn(BaseModel):
    flexible_share: float = Field(0.5, ge=0, le=1)  # share of shiftable jobs that may wait
    slack_hours: float = Field(12, ge=0, le=72)  # how long they may wait
    kw_per_gpu: float = Field(1.25, gt=0, le=10)
    cluster_gpus: int = Field(1000, ge=1, le=1_000_000)
    shift_capacity_share: float = Field(0.5, gt=0, le=1)


class BacktestIn(BaseModel):
    source: Literal["sample", "upload"]
    csv: str | None = Field(None, max_length=5_000_000)
    retime: bool = False  # move a file from another period onto the price window, keeping weekdays and times of day
    assumptions: BacktestAssumptionsIn = BacktestAssumptionsIn()


@app.get("/backtest/sample")
def backtest_sample():
    return backtest_runs.sample_info()


@app.post("/backtest", status_code=202, dependencies=[Depends(require_api_key)])
def backtest_start(body: BacktestIn):
    """Replay past jobs against real IEX prices and the official tariff (offline: nothing is run or stored)."""
    try:
        jobs = backtest_runs.prepare(body.source, body.csv, body.retime)
    except BacktestInputError as e:
        raise HTTPException(422, e.messages)
    run_id = backtest_runs.start(jobs, backtest_runs.default_assumptions(**body.assumptions.model_dump()), body.retime)
    if run_id is None:
        raise HTTPException(429, "Other reports are still running. Try again in a minute.")
    return {"id": run_id}


@app.get("/backtest/{run_id}")
def backtest_status(run_id: str):
    run = backtest_runs.get(run_id)
    if run is None:
        raise HTTPException(404, "report not found (reports are kept only for a short time)")
    return run


@app.get("/savings/summary")
def savings_summary(session: Session = Depends(get_session), now: datetime = Depends(get_now)):
    return summary(session, now)


@app.get("/measured")
def measured_runs(session: Session = Depends(get_session), now: datetime = Depends(get_now)):
    """The two real Kaggle runs of each job (without / with Wattshift), their measured power, and what each costs per GPU-hour."""
    return measured.build(session, now)


@app.get("/jobs", response_model=list[JobOut])
def list_jobs(session: Session = Depends(get_session)):
    rules, savings = load_rules(session), savings_by_job(session)
    jobs = session.scalars(select(Job).order_by(Job.submitted_at.desc(), Job.id))
    return [to_out(j, rules, savings) for j in jobs]


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return to_out(job, load_rules(session), {job.id: s} if (s := session.get(SavingsLog, job.id)) else None)


@app.get("/tariff")
def get_tariff(session: Session = Depends(get_session), now: datetime = Depends(get_now)):
    """The ToD rules the scheduler and dashboard use. `verified` means checked against the MERC order AND still within
    the period that order covers (MERC re-sets the numbers every 1 April)."""
    return {
        "base_rate_rs_kwh": settings.base_rate_rs_kwh,
        "verified": settings.tariff_verified and now.astimezone(IST).date() <= settings.tariff_valid_until,
        "valid_until": settings.tariff_valid_until.isoformat(),
        "source": settings.tariff_source,
        "notes": list(settings.tariff_notes),
        "season_now": season_of(now.astimezone(IST)),
        "rules": [
            {"zone": r.zone, "start_hour": r.start_hour, "end_hour": r.end_hour, "season": r.season, "adj_pct": r.rate_adjustment_pct}
            for r in load_rules(session)
        ],
    }


def require_site(x_site_key: str | None = Header(None), session: Session = Depends(get_session)) -> Site:
    site = sites.site_for_key(session, x_site_key or "")
    if site is None:
        raise HTTPException(401, "missing or invalid X-Site-Key")
    return site


@app.post("/agent/v1/sync", response_model=agent_sync.SyncOut)
def agent_sync_endpoint(
    body: agent_sync.SyncIn, site: Site = Depends(require_site), session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
):
    """The site agent reports its jobs and gets start-time decisions back. Idempotent: safe to retry."""
    out = agent_sync.process_sync(session, site, body, now)
    session.commit()
    return out


class SiteModeIn(BaseModel):
    mode: Literal["shadow", "autonomous"]


class ReleaseAllIn(BaseModel):
    on: bool


def _site_or_404(session: Session, site_id: uuid.UUID) -> Site:
    site = session.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "site not found")
    return site


def _site_state(site: Site) -> dict:
    return {"site_id": str(site.id), "mode": site.mode, "release_all": site.release_all}


@app.post("/sites/{site_id}/mode", dependencies=[Depends(require_api_key)])
def set_site_mode(site_id: uuid.UUID, body: SiteModeIn, session: Session = Depends(get_session), now: datetime = Depends(get_now)):
    site = _site_or_404(session, site_id)
    sites.set_mode(session, site, body.mode, now)
    session.commit()
    return _site_state(site)


@app.post("/sites/{site_id}/release-all", dependencies=[Depends(require_api_key)])
def set_site_release_all(site_id: uuid.UUID, body: ReleaseAllIn, session: Session = Depends(get_session), now: datetime = Depends(get_now)):
    """The kill switch: the agent releases every job it deferred and the cloud plans nothing until it is turned off."""
    site = _site_or_404(session, site_id)
    sites.set_release_all(session, site, body.on, now)
    session.commit()
    return _site_state(site)


@app.get("/sites")
def list_sites(session: Session = Depends(get_session)):
    rows = session.execute(select(Site, Company.name).join(Company, Company.id == Site.company_id).order_by(Company.name, Site.name)).all()
    return [
        {"id": str(s.id), "company": c, "name": s.name, "mode": s.mode, "release_all": s.release_all, "gpus": s.gpus,
         "last_seen_at": s.last_seen_at, "agent_version": s.agent_version}
        for s, c in rows
    ]


@app.get("/sites/{site_id}/view")
def site_view(site_id: uuid.UUID, session: Session = Depends(get_session), now: datetime = Depends(get_now)):
    """Everything the Live cluster page shows for one site, in one call."""
    return site_views.build_view(session, _site_or_404(session, site_id), now)
