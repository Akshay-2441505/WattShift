import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# Secrets live outside the OneDrive-synced project folder.
ENV_FILE = Path(os.environ.get("WATTSHIFT_ENV_FILE", Path.home() / ".wattshift" / ".env"))
load_dotenv(ENV_FILE)


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# MODELED, not measured: a 0-100 grid carbon-intensity index by IST hour, from the shape of India's solar duck curve
# (clean midday solar, dirty evening peak). Shown as a "modeled estimate"; replace with a real feed later.
CARBON_PROXY = (85, 85, 85, 85, 85, 85, 80, 75, 65, 50, 38, 30, 28, 28, 32, 45, 65, 85, 95, 95, 92, 90, 88, 86)


@dataclass(frozen=True)
class Settings:
    # repr=False: a settings object that is printed or logged (a common debugging line) must not spill credentials.
    database_url: str | None = field(default=os.environ.get("DATABASE_URL"), repr=False)
    test_database_url: str | None = field(default=os.environ.get("TEST_DATABASE_URL"), repr=False)
    # MSEDCL HT Industry (I-A) energy charge for FY 2026-27, Rs/kVAh (≈ kWh at unity power factor): MERC Case No. 75 of
    # 2025, order of 25 Mar 2026, p.100 (effective 1 Apr 2026). ToD % applies to this energy charge only; the Rs 0.81
    # wheeling charge and Rs 650/kVA/month fixed charge do not vary by time of day, so they are not part of any saving.
    base_rate_rs_kwh: float = _f("BASE_RATE_RS_KWH", 8.44)
    # The numbers in seed.py and BASE_RATE_RS_KWH were checked against the MERC order on 2026-09-19 (see seed.py). MERC
    # changes them every 1 April, so /tariff reports them as unverified after tariff_valid_until; set TARIFF_VERIFIED=0
    # to force the warning, e.g. after overriding BASE_RATE_RS_KWH with an unchecked value.
    tariff_verified: bool = os.environ.get("TARIFF_VERIFIED", "1") == "1"
    tariff_valid_until: date = date(2027, 3, 31)
    tariff_source: str = "MERC Case No. 75 of 2025, order dated 25 Mar 2026 (FY 2026-27, HT I-A Industry)"
    tariff_notes: tuple = (
        "Peak +25% for HT Industrial comes from the 28 Mar 2025 order (Case 217 of 2024); Table 8 of the 25 Mar 2026 "
        "order shows +20% with its footnote text missing.",
        "Energy charge is per kVAh; wheeling (Rs 0.81) and fixed charges are excluded because they do not vary by time.",
    )
    default_power_kw: float = _f("DEFAULT_POWER_KW", 10.0)  # ~ one 8-GPU node; modeled, not measured
    cap_minutes_per_block: int = int(_f("CAP_MINUTES_PER_BLOCK", 30))  # 2 concurrent job-slots per 15 min
    freeze_minutes: int = int(_f("FREEZE_MINUTES", 10))
    demo_mode: bool = os.environ.get("DEMO_MODE") == "1"
    api_key: str | None = field(default=os.environ.get("API_KEY"), repr=False)  # guards mutating endpoints; unset = open (local dev only)
    production: bool = os.environ.get("PRODUCTION") == "1"  # hosted: refuses to start without API_KEY
    cors_origins: tuple = tuple(o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip())  # the dashboard's origin(s)
    run_scheduler: bool = os.environ.get("SCHEDULER") == "1"  # start dispatch/ingest loops inside the API process
    dispatch_seconds: float = _f("DISPATCH_SECONDS", 5)  # lower (e.g. 2) for fast-clock demos
    # With no compute provider (a hosted API cannot run Kaggle jobs) the dispatch loop has nothing to start, so it only needs to
    # expire overdue jobs now and then. A 5-second loop would keep a scale-to-zero database awake all month.
    idle_dispatch_seconds: float = _f("IDLE_DISPATCH_SECONDS", 1800)
    replan_seconds: float = _f("REPLAN_SECONDS", 300)  # how often not-yet-run jobs are re-evaluated (F9)
    # Jobs allowed to run at once. Kaggle's free tier appears to allow ~2 concurrent GPU sessions: with 4 at once the
    # extra pushes never became kernels (observed in the replay demo).
    max_concurrent_runs: int = int(_f("MAX_CONCURRENT_RUNS", 2))
    # Jobs allowed to wait or run at once. Every Kaggle job costs two GPU runs, so an unbounded queue is an unbounded bill
    # in quota for whoever holds the API key (or anyone, when none is set).
    max_active_jobs: int = int(_f("MAX_ACTIVE_JOBS", 50))
    kaggle_username: str | None = os.environ.get("KAGGLE_USERNAME")  # not secret; the token lives in ~/.kaggle
    kaggle_run_seconds: int = int(_f("KAGGLE_RUN_SECONDS", 30))  # real GPU burst per job (modeled duration is separate)
    kaggle_timeout_seconds: int = int(_f("KAGGLE_TIMEOUT_SECONDS", 600))  # hard cap passed to `kaggle kernels push -t`


settings = Settings()
