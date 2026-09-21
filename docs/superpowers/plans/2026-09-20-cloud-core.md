# Cloud core (sites, tariff catalogue, sync endpoint, planner) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The cloud half of the Slurm automation: an agent (or a test) posts job facts to `POST /agent/v1/sync` and gets back start-time decisions, with shadow/autonomous modes, a release-all kill switch, per-site tariffs and an audit trail. It is fully testable with no agent, by posting recorded job facts.

**Architecture:** New tables sit beside the existing demo tables (which stay untouched). A pure-ish planner (`app/planner.py`) reuses `allocate()` (with its GPU `weight`), `bill_cost()` and the price and tariff code. A sync service (`app/agent_sync.py`) applies the agent's report, runs the planner under a per-site advisory lock and returns the decisions. Everything is keyed by `(site, ref)` so a retried request changes nothing.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, psycopg3, Postgres (Neon `wattshift` and `wattshift_tests`), pytest. Nothing new to install.

**Spec:** `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md` (sections 6, 7, 8, 9, 12) and the evidence in `docs/superpowers/spikes/2026-09-19-slurm-spike-findings.md`.

## Global Constraints

- All times are UTC in the API and the database (`TIMESTAMPTZ`); the ToD tariff is evaluated in IST through the existing `tod_multiplier`.
- Hard limit: a decision is never later than `baseline_start + max_wait - start_margin`; `start_margin` defaults to **120 s** per site.
- `baseline_start` is Slurm's predicted start only if present and `<= first_seen + max_wait`; otherwise `first_seen` (Spike 0: the prediction is empty at first and reads exactly +1 year when running jobs have no time limit).
- `gpus` is stored once, at first sight, and never overwritten.
- A job is deferred only when the bill at the planned start is **strictly lower** than at the baseline start. Otherwise it runs as Slurm decides and no decision is sent.
- Shadow mode: the cloud plans and records decisions but returns **none**. Effective mode is `autonomous` only if the site row and the agent's report both say so.
- Nothing is ever held: a job with no decision (unplaceable, skipped, abandoned, released) runs exactly as it would have without Wattshift.
- Existing tables (`jobs`, `savings_log`, `price_signals`, `tod_schedule`) and the demo dispatcher are not changed. The existing 176 backend tests must still pass.
- The audit trail and `decisions` are append-only by convention: only inserts exist in code. `# ponytail: no DB trigger enforcing append-only; add one (or revoke UPDATE/DELETE) before a real customer.`
- No commits: the repo has no commits yet. Commit only when the user asks.

## File structure

| File | Responsibility |
|---|---|
| `backend/app/models.py` (modify) | Seven new tables: `companies`, `sites`, `site_keys`, `tariff_catalogue`, `managed_jobs`, `decisions`, `audit_log` |
| `backend/app/audit.py` (create) | `log()` and `log_throttled()`, insert-only |
| `backend/app/catalogue.py` (create) | `Tariff`, `seed_catalogue()`, `get_tariff()` |
| `backend/app/sites.py` (create) | Onboarding, key hashing and lookup, mode and release-all switches |
| `backend/app/planner.py` (create) | Baseline guard, site capacity, `plan_site()` |
| `backend/app/agent_sync.py` (create) | Request/response schemas and `process_sync()` |
| `backend/app/service.py` (modify) | `_book()` gains a `weight` argument (default 1, so nothing else changes) |
| `backend/app/main.py` (modify) | `POST /agent/v1/sync`, `POST /sites/{id}/mode`, `POST /sites/{id}/release-all` |
| `backend/scripts/init_db.py` (modify) | Seed the catalogue |
| `backend/scripts/onboard_site.py` (create) | Create a company and site and print its key once |
| `backend/tests/test_managed_models.py`, `test_catalogue.py`, `test_sites.py`, `test_planner.py`, `test_agent_sync.py`, `test_agent_api.py` (create) | Tests |

## Contract additions to the spec (made by this plan; Task 7 writes them into the spec)

The spec's request has `jobs` and `applied`. Spike 0's "stop managing on user change" and the kill switch need four more fields:

- job field `override` (bool): the agent saw a change it did not make; the cloud abandons the job.
- job field `skipped_reason` (string): array, dependency or requeue; the cloud never plans it.
- request field `released` (list of refs): jobs the agent set to start now after `release_all`.
- request field `release_all` (bool): the operator ran `release-all` on the agent; the cloud sets the site's kill switch.
- Cloud-side `plan_status`: `pending | planned | unplaceable | skipped | abandoned | released`.

Job `state` is one of `PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT, OTHER` (the agent maps Slurm's longer list by prefix, everything else is `OTHER`).

---

### Task 1: Tables

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/tests/test_managed_models.py`

**Interfaces:**
- Produces: ORM classes `Company`, `Site`, `SiteKey`, `TariffCatalogue`, `ManagedJob`, `Decision`, `AuditLog` in `app.models`, with the column names used by every later task.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_managed_models.py`:

```python
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import AuditLog, Company, Decision, ManagedJob, Site, SiteKey, TariffCatalogue

NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def make_site(session, **kw):
    c = Company(name="Acme")
    session.add(c)
    session.flush()
    s = Site(company_id=c.id, name="Pune-1", gpus=8, **kw)
    session.add(s)
    session.flush()
    return s


def make_job(session, site, ref="1", **kw):
    j = ManagedJob(site_id=site.id, ref=ref, state="PENDING", first_seen=NOW, submit_time=NOW, **kw)
    session.add(j)
    session.flush()
    return j


def test_site_defaults(session):
    s = make_site(session)
    session.refresh(s)
    assert s.mode == "shadow" and s.release_all is False
    assert float(s.kw_per_gpu) == 1.25 and float(s.shift_capacity_share) == 0.5
    assert s.start_margin_s == 120 and s.utility == "MSEDCL" and s.tariff_category == "HT-I(A)"
    assert s.power_limit_kw is None and s.last_seen_at is None


def test_managed_job_defaults(session):
    j = make_job(session, make_site(session))
    session.refresh(j)
    assert j.id is not None and j.plan_status == "pending" and j.applied_start is None


def test_managed_job_is_unique_per_site_and_ref(session):
    site = make_site(session)
    make_job(session, site, "48211")
    session.add(ManagedJob(site_id=site.id, ref="48211", state="PENDING", first_seen=NOW, submit_time=NOW))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("kw", [{"mode": "weird"}, {"gpus": 0}])
def test_site_constraints(session, kw):
    c = Company(name="Acme")
    session.add(c)
    session.flush()
    session.add(Site(**{"company_id": c.id, "name": "X", "gpus": 8, **kw}))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("kw", [{"state": "WEIRD"}, {"plan_status": "weird"}])
def test_managed_job_constraints(session, kw):
    site = make_site(session)
    base = {"site_id": site.id, "ref": "1", "state": "PENDING", "first_seen": NOW, "submit_time": NOW}
    session.add(ManagedJob(**{**base, **kw}))
    with pytest.raises(IntegrityError):
        session.flush()


def test_other_tables_round_trip(session):
    site = make_site(session)
    job = make_job(session, site)
    session.add_all([
        SiteKey(site_id=site.id, key_hash="abc"),
        TariffCatalogue(utility="MSEDCL", category="HT-I(A)", valid_from=NOW.date(), valid_until=NOW.date(),
                        rules=[{"zone": "baseline"}], base_rate=8.44, verified=True, source="test"),
        Decision(managed_job_id=job.id, site_id=site.id, created_at=NOW, start_at=NOW, mode="shadow"),
        AuditLog(site_id=site.id, at=NOW, actor="planner", event="decision", ref="1", detail={"a": 1}),
    ])
    session.flush()
    assert session.query(AuditLog).one().detail == {"a": 1}
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_managed_models.py -q`
Expected: FAIL with `ImportError: cannot import name 'AuditLog'`.

- [ ] **Step 3: Add the tables**

In `backend/app/models.py` replace the two import lines

```python
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, SmallInteger, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
```

with

```python
from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Numeric, SmallInteger, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
```

and append at the end of the file:

```python
# --- Slurm automation (managed by the site agent) ----------------------------------------------------------------


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Site(Base):
    """One customer cluster. Facts are entered once at onboarding."""

    __tablename__ = "sites"
    __table_args__ = (
        CheckConstraint("mode in ('shadow','autonomous')", name="sites_mode_valid"),
        CheckConstraint("gpus > 0", name="sites_gpus_positive"),
        UniqueConstraint("company_id", "name"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(Text)
    utility: Mapped[str] = mapped_column(Text, default="MSEDCL", server_default="MSEDCL")
    tariff_category: Mapped[str] = mapped_column(Text, default="HT-I(A)", server_default="HT-I(A)")
    gpus: Mapped[int]
    power_limit_kw: Mapped[Decimal | None] = mapped_column(Numeric)  # max shifted load; NULL = no site limit
    kw_per_gpu: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("1.25"), server_default="1.25")  # modeled
    shift_capacity_share: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.5"), server_default="0.5")
    start_margin_s: Mapped[int] = mapped_column(default=120, server_default="120")
    mode: Mapped[str] = mapped_column(Text, default="shadow", server_default="shadow")
    release_all: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # kill switch
    last_seen_at: Mapped[datetime | None] = mapped_column(TS)
    last_agent_mode: Mapped[str | None] = mapped_column(Text)
    agent_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class SiteKey(Base):
    """Only the SHA-256 of a site's key is stored; the key itself is shown once at issue."""

    __tablename__ = "site_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class TariffCatalogue(Base):
    """One verified, versioned, expiring tariff per utility and category."""

    __tablename__ = "tariff_catalogue"
    __table_args__ = (UniqueConstraint("utility", "category", "valid_from"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    utility: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date] = mapped_column(Date)  # inclusive
    rules: Mapped[list] = mapped_column(JSONB)  # [{zone, start_hour, end_hour, season, adj_pct}]
    base_rate: Mapped[Decimal] = mapped_column(Numeric)  # Rs/kWh energy charge
    verified: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(Text)


class ManagedJob(Base):
    """A customer job the agent reported. `state` is the Slurm state; `plan_status` is what Wattshift does about it."""

    __tablename__ = "managed_jobs"
    __table_args__ = (
        UniqueConstraint("site_id", "ref"),
        CheckConstraint(
            "state in ('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED','TIMEOUT','OTHER')", name="managed_state_valid"
        ),
        CheckConstraint(
            "plan_status in ('pending','planned','unplaceable','skipped','abandoned','released')",
            name="managed_plan_status_valid",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    ref: Mapped[str] = mapped_column(Text)  # the Slurm job id
    state: Mapped[str] = mapped_column(Text)
    plan_status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    note: Mapped[str | None] = mapped_column(Text)  # why unplaceable / skipped / abandoned
    submit_time: Mapped[datetime] = mapped_column(TS)
    first_seen: Mapped[datetime] = mapped_column(TS)
    gpus: Mapped[int | None]
    time_limit_min: Mapped[int | None]
    max_wait_min: Mapped[int | None]
    predicted_start: Mapped[datetime | None] = mapped_column(TS)  # as reported; may be implausible
    baseline_start: Mapped[datetime | None] = mapped_column(TS)  # when it would have started without Wattshift
    planned_start: Mapped[datetime | None] = mapped_column(TS)  # what we want Slurm to use; NULL = let Slurm decide
    applied_start: Mapped[datetime | None] = mapped_column(TS)  # what the agent confirmed it set
    actual_start: Mapped[datetime | None] = mapped_column(TS)
    actual_end: Mapped[datetime | None] = mapped_column(TS)
    baseline_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    planned_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    actual_cost: Mapped[Decimal | None] = mapped_column(MONEY)  # filled by the measurement plan
    saved: Mapped[Decimal | None] = mapped_column(MONEY)  # filled by the measurement plan


class Decision(Base):
    """Append-only: one row each time the planned start of a job changes to a new deferral."""

    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    managed_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_jobs.id"))
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    created_at: Mapped[datetime] = mapped_column(TS)
    start_at: Mapped[datetime] = mapped_column(TS)
    mode: Mapped[str] = mapped_column(Text)  # shadow | autonomous (what the effective mode was)
    baseline_start: Mapped[datetime | None] = mapped_column(TS)
    baseline_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    planned_cost: Mapped[Decimal | None] = mapped_column(MONEY)


class AuditLog(Base):
    """Append-only trail of everything that matters: who did what to which job and when."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    at: Mapped[datetime] = mapped_column(TS)
    actor: Mapped[str] = mapped_column(Text)  # agent | planner | operator
    event: Mapped[str] = mapped_column(Text)
    ref: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)
```

Also change the second import line of the file from `from datetime import datetime` to `from datetime import date, datetime`.

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_managed_models.py -q`
Expected: `7 passed` (the parametrized tests count separately; all green).

Also run `.\.venv\Scripts\python -m pytest tests/test_models.py -q` to confirm the old tables still work. Expected: pass.

---

### Task 2: Tariff catalogue

**Files:**
- Create: `backend/app/catalogue.py`, `backend/tests/test_catalogue.py`
- Modify: `backend/scripts/init_db.py`

**Interfaces:**
- Consumes: `TariffCatalogue` (Task 1), `TOD_SEED` and `TodRule` (existing), `settings` (existing).
- Produces: `Tariff(utility, category, rules: list[TodRule], base_rate: float, verified: bool, valid_until: date, source: str)`; `seed_catalogue(session) -> None` (idempotent); `get_tariff(session, utility: str, category: str, on: date) -> Tariff` (raises `LookupError` when no entry covers `on`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_catalogue.py`:

```python
from datetime import date, datetime

import pytest

from app.catalogue import get_tariff, seed_catalogue
from app.config import settings
from app.models import TariffCatalogue
from app.seed import TOD_SEED
from app.tariff import IST, tod_multiplier


def test_seeded_entry_matches_the_verified_tariff(session):
    seed_catalogue(session)
    t = get_tariff(session, "MSEDCL", "HT-I(A)", date(2026, 9, 20))
    assert t.rules == TOD_SEED
    assert t.base_rate == 8.44 and t.verified and t.valid_until == settings.tariff_valid_until
    assert tod_multiplier(datetime(2026, 7, 1, 19, tzinfo=IST), t.rules) == pytest.approx(1.25)


def test_seeding_twice_keeps_one_row(session):
    seed_catalogue(session)
    seed_catalogue(session)
    assert session.query(TariffCatalogue).count() == 1


@pytest.mark.parametrize("on", [date(2026, 3, 31), date(2027, 4, 1)])  # the day before it starts; the day after it expires
def test_outside_validity_is_an_error(session, on):
    seed_catalogue(session)
    with pytest.raises(LookupError):
        get_tariff(session, "MSEDCL", "HT-I(A)", on)


def test_last_valid_day_is_included(session):
    seed_catalogue(session)
    assert get_tariff(session, "MSEDCL", "HT-I(A)", settings.tariff_valid_until).valid_until == settings.tariff_valid_until


def test_unknown_category_is_an_error(session):
    seed_catalogue(session)
    with pytest.raises(LookupError):
        get_tariff(session, "MSEDCL", "LT-V", date(2026, 9, 20))
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_catalogue.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.catalogue'`.

- [ ] **Step 3: Write the module**

Create `backend/app/catalogue.py`:

```python
"""Tariff catalogue: one verified, versioned, expiring entry per utility and category (spec section 11)."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import TariffCatalogue
from app.seed import TOD_SEED
from app.tariff import TodRule

MSEDCL_HT_IA_FROM = date(2026, 4, 1)  # FY 2026-27 starts here; expiry comes from settings.tariff_valid_until


@dataclass(frozen=True)
class Tariff:
    utility: str
    category: str
    rules: list[TodRule]
    base_rate: float  # Rs/kWh energy charge
    verified: bool
    valid_until: date
    source: str


def _rules_to_json(rules: list[TodRule]) -> list[dict]:
    return [
        {"zone": r.zone, "start_hour": r.start_hour, "end_hour": r.end_hour, "season": r.season, "adj_pct": r.rate_adjustment_pct}
        for r in rules
    ]


def _rules_from_json(rows: list[dict]) -> list[TodRule]:
    return [TodRule(r["zone"], r["start_hour"], r["end_hour"], r["season"], float(r["adj_pct"])) for r in rows]


def seed_catalogue(session: Session) -> None:
    """Insert or refresh the verified MSEDCL HT-I(A) FY 2026-27 entry from seed.py and settings. Safe to re-run."""
    values = dict(
        valid_until=settings.tariff_valid_until, rules=_rules_to_json(TOD_SEED), base_rate=settings.base_rate_rs_kwh,
        verified=settings.tariff_verified, source=settings.tariff_source,
    )
    row = session.scalar(
        select(TariffCatalogue).where(
            TariffCatalogue.utility == "MSEDCL", TariffCatalogue.category == "HT-I(A)",
            TariffCatalogue.valid_from == MSEDCL_HT_IA_FROM,
        )
    )
    if row is None:
        session.add(TariffCatalogue(utility="MSEDCL", category="HT-I(A)", valid_from=MSEDCL_HT_IA_FROM, **values))
    else:
        for k, v in values.items():
            setattr(row, k, v)
    session.flush()


def get_tariff(session: Session, utility: str, category: str, on: date) -> Tariff:
    """The entry valid on `on` (an IST calendar date). Raises LookupError if none is: callers then plan nothing."""
    row = session.scalar(
        select(TariffCatalogue)
        .where(
            TariffCatalogue.utility == utility, TariffCatalogue.category == category,
            TariffCatalogue.valid_from <= on, TariffCatalogue.valid_until >= on,
        )
        .order_by(TariffCatalogue.valid_from.desc())
    )
    if row is None:
        raise LookupError(f"no tariff for {utility} {category} on {on}")
    return Tariff(row.utility, row.category, _rules_from_json(row.rules), float(row.base_rate), row.verified, row.valid_until, row.source)
```

(`2027-04-01` is the day after `settings.tariff_valid_until`, which is `2027-03-31`.)

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_catalogue.py -q`
Expected: all pass.

- [ ] **Step 5: Seed it in `init_db`**

In `backend/scripts/init_db.py` change the import `from app.seed import seed_tod` to add `from app.catalogue import seed_catalogue` above it, and add `seed_catalogue(s)` on the line after `seed_tod(s)`. Update the docstring to say "seed the ToD tariff and the tariff catalogue".

Run against the dev database (it creates the new tables too and fetches IEX prices, the same two page requests as before):

Run: `cd backend; $env:PYTHONPATH=(Get-Location).Path; .\.venv\Scripts\python -m scripts.init_db`
Expected: prints `ingested ... blocks; price_signals now ... rows`. No error.

---

### Task 3: Sites, keys and the audit helper

**Files:**
- Create: `backend/app/audit.py`, `backend/app/sites.py`, `backend/scripts/onboard_site.py`, `backend/tests/test_sites.py`

**Interfaces:**
- Consumes: `Company`, `Site`, `SiteKey`, `AuditLog` (Task 1).
- Produces:
  - `audit.log(session, site_id, now, actor: str, event: str, ref: str | None = None, **detail) -> None`
  - `audit.log_throttled(session, site_id, now, actor, event, *, minutes: int = 60, ref=None, **detail) -> None` (skips if the same event was logged for the site in the last `minutes`)
  - `sites.create_site(session, company_name, site_name, *, gpus, power_limit_kw=None, kw_per_gpu=1.25, shift_capacity_share=0.5, utility="MSEDCL", tariff_category="HT-I(A)", mode="shadow") -> tuple[Site, str]` (the second value is the plaintext key, shown once)
  - `sites.issue_key(session, site) -> str`, `sites.revoke_keys(session, site, now) -> None`, `sites.site_for_key(session, key: str) -> Site | None`
  - `sites.set_mode(session, site, mode: str, now, actor="operator") -> None`, `sites.set_release_all(session, site, on: bool, now, actor="operator") -> None`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sites.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from app import audit, sites
from app.models import AuditLog, SiteKey

NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def test_create_site_returns_a_key_that_finds_it(session):
    site, key = sites.create_site(session, "Acme", "Pune-1", gpus=64)
    assert key.startswith("wsk_") and len(key) > 30
    assert sites.site_for_key(session, key).id == site.id
    assert site.mode == "shadow" and site.gpus == 64


def test_only_the_hash_is_stored(session):
    _, key = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    stored = session.query(SiteKey).one().key_hash
    assert stored != key and key not in stored and len(stored) == 64


def test_unknown_or_empty_key_finds_nothing(session):
    sites.create_site(session, "Acme", "Pune-1", gpus=8)
    assert sites.site_for_key(session, "wsk_nope") is None
    assert sites.site_for_key(session, "") is None


def test_revoked_key_stops_working_and_a_new_one_works(session):
    site, old = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    sites.revoke_keys(session, site, NOW)
    assert sites.site_for_key(session, old) is None
    assert sites.site_for_key(session, sites.issue_key(session, site)).id == site.id


def test_second_site_reuses_the_company(session):
    a, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    b, _ = sites.create_site(session, "Acme", "Nagpur-1", gpus=8)
    assert a.company_id == b.company_id


def test_set_mode_and_release_all_are_audited(session):
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    sites.set_mode(session, site, "autonomous", NOW)
    sites.set_release_all(session, site, True, NOW)
    assert site.mode == "autonomous" and site.release_all is True
    events = [(a.actor, a.event) for a in session.query(AuditLog).order_by(AuditLog.id)]
    assert events == [("operator", "mode_changed"), ("operator", "release_all_on")]


def test_bad_mode_is_rejected(session):
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    with pytest.raises(ValueError):
        sites.set_mode(session, site, "yolo", NOW)


def test_log_throttled_skips_a_repeat_inside_the_window(session):
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    audit.log_throttled(session, site.id, NOW, "planner", "no_tariff")
    audit.log_throttled(session, site.id, NOW + timedelta(minutes=30), "planner", "no_tariff")
    audit.log_throttled(session, site.id, NOW + timedelta(minutes=61), "planner", "no_tariff")
    assert session.query(AuditLog).count() == 2
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_sites.py -q`
Expected: FAIL, `ImportError: cannot import name 'audit' from 'app'`.

- [ ] **Step 3: Write the modules**

Create `backend/app/audit.py`:

```python
"""Append-only audit trail. This module only ever inserts."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog


def log(session: Session, site_id, now: datetime, actor: str, event: str, ref: str | None = None, **detail) -> None:
    """`detail` values must be JSON-serialisable (pass datetimes as isoformat strings)."""
    session.add(AuditLog(site_id=site_id, at=now, actor=actor, event=event, ref=ref, detail=detail or None))


def log_throttled(
    session: Session, site_id, now: datetime, actor: str, event: str, *, minutes: int = 60, ref: str | None = None, **detail
) -> None:
    """Like log(), but skipped if this site already has the same event in the last `minutes`, so a 30 s poll cannot
    flood the trail with a standing condition (no tariff, mode mismatch)."""
    session.flush()
    recent = session.scalar(
        select(AuditLog.id)
        .where(AuditLog.site_id == site_id, AuditLog.event == event, AuditLog.at > now - timedelta(minutes=minutes))
        .limit(1)
    )
    if recent is None:
        log(session, site_id, now, actor, event, ref, **detail)
```

Create `backend/app/sites.py`:

```python
"""Onboarding and access for customer sites: companies, sites, per-site keys, and the two operator switches."""
import hashlib
import secrets
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit
from app.models import Company, Site, SiteKey

KEY_PREFIX = "wsk_"
MODES = ("shadow", "autonomous")


def hash_key(key: str) -> str:
    # Keys are 256-bit random tokens, so a plain SHA-256 is enough (no salt or slow hash needed).
    return hashlib.sha256(key.encode()).hexdigest()


def issue_key(session: Session, site: Site) -> str:
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    session.add(SiteKey(site_id=site.id, key_hash=hash_key(key)))
    session.flush()
    return key


def revoke_keys(session: Session, site: Site, now: datetime) -> None:
    session.execute(update(SiteKey).where(SiteKey.site_id == site.id, SiteKey.revoked_at.is_(None)).values(revoked_at=now))


def site_for_key(session: Session, key: str) -> Site | None:
    if not key:
        return None
    return session.scalar(
        select(Site).join(SiteKey, SiteKey.site_id == Site.id).where(SiteKey.key_hash == hash_key(key), SiteKey.revoked_at.is_(None))
    )


def create_site(
    session: Session, company_name: str, site_name: str, *, gpus: int, power_limit_kw: float | None = None,
    kw_per_gpu: float = 1.25, shift_capacity_share: float = 0.5, utility: str = "MSEDCL",
    tariff_category: str = "HT-I(A)", mode: str = "shadow",
) -> tuple[Site, str]:
    """Create (or reuse) the company, add the site, issue its first key. The plaintext key is returned once."""
    company = session.scalar(select(Company).where(Company.name == company_name))
    if company is None:
        company = Company(name=company_name)
        session.add(company)
        session.flush()
    site = Site(
        company_id=company.id, name=site_name, gpus=gpus, power_limit_kw=power_limit_kw, kw_per_gpu=kw_per_gpu,
        shift_capacity_share=shift_capacity_share, utility=utility, tariff_category=tariff_category, mode=mode,
    )
    session.add(site)
    session.flush()
    return site, issue_key(session, site)


def set_mode(session: Session, site: Site, mode: str, now: datetime, actor: str = "operator") -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if site.mode != mode:
        audit.log(session, site.id, now, actor, "mode_changed", previous=site.mode, mode=mode)
        site.mode = mode
        session.flush()


def set_release_all(session: Session, site: Site, on: bool, now: datetime, actor: str = "operator") -> None:
    """The kill switch: while on, the cloud plans nothing and tells the agent to release every job it deferred."""
    if site.release_all != on:
        audit.log(session, site.id, now, actor, "release_all_on" if on else "release_all_off")
        site.release_all = on
        session.flush()
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_sites.py -q`
Expected: all pass.

- [ ] **Step 5: The onboarding command**

Create `backend/scripts/onboard_site.py`:

```python
"""Create a company and site and print its key ONCE.  Usage:
python -m scripts.onboard_site --company Acme --site Pune-1 --gpus 64 [--power-limit-kw 400] [--kw-per-gpu 1.25] [--mode shadow]"""
import argparse

from app import db, models, sites
from app.config import settings


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--company", required=True)
    p.add_argument("--site", required=True)
    p.add_argument("--gpus", type=int, required=True)
    p.add_argument("--power-limit-kw", type=float)
    p.add_argument("--kw-per-gpu", type=float, default=1.25)
    p.add_argument("--mode", choices=sites.MODES, default="shadow")
    a = p.parse_args()
    assert settings.database_url, "DATABASE_URL not set (see ~/.wattshift/.env)"
    engine = db.make_engine(settings.database_url)
    db.Base.metadata.create_all(engine)
    with db.make_session_factory(engine)() as s, s.begin():
        site, key = sites.create_site(
            s, a.company, a.site, gpus=a.gpus, power_limit_kw=a.power_limit_kw, kw_per_gpu=a.kw_per_gpu, mode=a.mode
        )
        print(f"site {site.id} created in {site.mode} mode.")
        print(f"agent key (shown once, store it in the agent's config): {key}")


if __name__ == "__main__":
    main()
```

(`models` is imported so every table is registered on `db.Base` before `create_all`.) It is run by hand; `create_site` is what the tests cover.

---

### Task 4: The planner

**Files:**
- Modify: `backend/app/service.py` (the `_book` function only)
- Create: `backend/app/planner.py`, `backend/tests/test_planner.py`

**Interfaces:**
- Consumes: `ManagedJob`, `Site`, `Decision` (Task 1), `Tariff` (Task 2), `audit.log` (Task 3), and existing `allocate`, `Block`, `NoCapacityError`, `floor_block`, `bill_cost`, `tod_multiplier`, `service._book`, `service.active_source`, `settings.freeze_minutes`.
- Produces:
  - `planner.plausible_predicted(predicted: datetime | None, first_seen: datetime, max_wait_min: int | None) -> datetime | None`
  - `planner.baseline_of(job: ManagedJob) -> datetime`
  - `planner.site_cap(site: Site) -> int` (GPU-minutes of shifted work allowed per 15-minute block)
  - `planner.plan_site(session, site, tariff: Tariff, *, now: datetime, mode: str) -> dict[str, int]` with keys `deferred`, `normal`, `unchanged`, `unplaceable`. It sets `plan_status`, `baseline_start`, `baseline_cost`, `planned_start`, `planned_cost` on jobs and inserts `Decision` rows and audit events. It does not commit. The caller must hold the site's advisory lock.

Behaviour, in order: RUNNING jobs that Wattshift deferred (`applied_start` set) book their capacity at `actual_start`; a PENDING job whose `applied_start` is within the freeze margin is pinned and keeps its capacity; every other PENDING job with `plan_status in (pending, planned, unplaceable)` is (re)planned in submission order. `skipped`, `abandoned` and `released` jobs are never touched. A job is deferred only if the bill at the chosen start is strictly lower than at the baseline start.

- [ ] **Step 1: Give `_book` a weight**

In `backend/app/service.py` replace

```python
def _book(ledger: dict[datetime, int], start: datetime, duration_minutes: int) -> None:
    for block, minutes in overlaps(start, duration_minutes):
        ledger[block] = ledger.get(block, 0) + minutes
```

with

```python
def _book(ledger: dict[datetime, int], start: datetime, duration_minutes: int, weight: int = 1) -> None:
    for block, minutes in overlaps(start, duration_minutes):
        ledger[block] = ledger.get(block, 0) + minutes * weight  # weight = GPUs, matching allocate(weight=...)
```

Run the existing suite for the two callers: `cd backend; .\.venv\Scripts\python -m pytest tests/test_service.py tests/test_replan.py -q`. Expected: pass (weight defaults to 1).

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_planner.py`:

```python
import uuid
from datetime import date, datetime, timedelta

import pytest

from app import planner, sites
from app.allocator import overlaps
from app.catalogue import get_tariff, seed_catalogue
from app.models import AuditLog, Decision, ManagedJob, PriceSignal
from app.seed import seed_tod
from tests.helpers import NOW, add_prices, evening_to_next_noon

CHEAP_START = NOW + timedelta(hours=15)  # 10:00 IST next day; the 4 cheap blocks run to 11:00
CHEAP_END = NOW + timedelta(hours=16)
MIN = timedelta(minutes=1)


@pytest.fixture()
def world(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon())
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)  # cap = 0.5 * 8 * 15 = 60 GPU-minutes per block
    tariff = get_tariff(session, "MSEDCL", "HT-I(A)", date(2026, 7, 1))
    return session, site, tariff


def add_job(session, site, ref, *, gpus=4, limit=30, wait=17 * 60, state="PENDING", first_seen=NOW, **kw):
    j = ManagedJob(
        id=uuid.uuid4(), site_id=site.id, ref=ref, state=state, first_seen=first_seen, submit_time=first_seen,
        gpus=gpus, time_limit_min=limit, max_wait_min=wait, **kw,
    )
    session.add(j)
    session.flush()
    return j


def plan(world, now=NOW, mode="autonomous"):
    session, site, tariff = world
    return planner.plan_site(session, site, tariff, now=now, mode=mode)


# --- baseline guard ---------------------------------------------------------------------------------------------

def test_a_prediction_a_year_out_is_ignored():
    assert planner.plausible_predicted(NOW + timedelta(days=365), NOW, 1440) is None


def test_missing_prediction_or_missing_max_wait_is_ignored():
    assert planner.plausible_predicted(None, NOW, 1440) is None
    assert planner.plausible_predicted(NOW + timedelta(hours=1), NOW, None) is None


def test_a_prediction_inside_the_wait_is_accepted():
    p = NOW + timedelta(hours=2)
    assert planner.plausible_predicted(p, NOW, 1440) == p


def test_baseline_falls_back_to_first_seen_and_never_precedes_it():
    j = ManagedJob(first_seen=NOW, max_wait_min=1440, predicted_start=NOW + timedelta(days=365))
    assert planner.baseline_of(j) == NOW
    j.predicted_start = NOW - timedelta(hours=1)
    assert planner.baseline_of(j) == NOW
    j.predicted_start = NOW + timedelta(hours=3)
    assert planner.baseline_of(j) == NOW + timedelta(hours=3)


def test_site_cap_uses_share_gpus_and_the_power_limit(session):
    a, _ = sites.create_site(session, "A", "s", gpus=8)
    assert planner.site_cap(a) == 60
    b, _ = sites.create_site(session, "B", "s", gpus=8, power_limit_kw=5, kw_per_gpu=1.25)  # 4 GPUs of power
    assert planner.site_cap(b) == 60  # min(60, 5 / 1.25 * 15 = 60)
    c, _ = sites.create_site(session, "C", "s", gpus=8, power_limit_kw=2.5, kw_per_gpu=1.25)  # 2 GPUs of power
    assert planner.site_cap(c) == 30


# --- planning ---------------------------------------------------------------------------------------------------

def test_defers_a_flexible_job_into_the_cheap_window(world):
    session, site, _ = world
    j = add_job(session, site, "1")
    stats = plan(world)
    assert stats["deferred"] == 1
    assert j.plan_status == "planned"
    assert CHEAP_START <= j.planned_start and j.planned_start + 30 * MIN <= CHEAP_END
    assert j.baseline_start == NOW
    assert float(j.planned_cost) < float(j.baseline_cost)
    d = session.query(Decision).one()
    assert d.start_at == j.planned_start and d.mode == "autonomous"


def test_never_later_than_baseline_plus_wait_minus_margin(world):
    session, site, _ = world
    site.start_margin_s = 3600  # a large margin makes the rule visible: latest start = NOW + 14 h - 1 h = 08:00 next day
    j = add_job(session, site, "1", wait=14 * 60)  # the cheap window (10:00) is out of reach
    plan(world)
    assert j.planned_start is None or j.planned_start <= NOW + timedelta(hours=13)


def test_a_predicted_baseline_moves_the_window(world):
    session, site, _ = world
    j = add_job(session, site, "1", predicted_start=NOW + timedelta(hours=2))
    plan(world)
    assert j.baseline_start == NOW + timedelta(hours=2)
    assert j.planned_start >= NOW + timedelta(hours=2)


def test_runs_as_normal_when_now_is_already_the_cheapest_bill(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon(cheap_from_idx=0))  # cheap right now; the bill is peak either way
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    tariff = get_tariff(session, "MSEDCL", "HT-I(A)", date(2026, 7, 1))
    j = add_job(session, site, "1")
    stats = planner.plan_site(session, site, tariff, now=NOW, mode="autonomous")
    assert stats["normal"] == 1 and stats["deferred"] == 0
    assert j.plan_status == "planned" and j.planned_start is None
    assert float(j.planned_cost) == pytest.approx(float(j.baseline_cost))
    assert session.query(Decision).count() == 0


def test_capacity_spreads_jobs_and_never_overbooks(world):
    session, site, _ = world
    jobs = [add_job(session, site, str(i), gpus=4, limit=15) for i in range(3)]  # 4 GPUs x 15 min = 60 = one full block
    plan(world)
    load: dict = {}
    for j in jobs:
        assert j.planned_start is not None
        for b, m in overlaps(j.planned_start, 15):
            load[b] = load.get(b, 0) + m * 4
    assert max(load.values()) <= planner.site_cap(site)
    assert len({j.planned_start for j in jobs}) == 3


def test_replanning_unchanged_prices_changes_nothing(world):
    session, site, _ = world
    jobs = [add_job(session, site, str(i), gpus=2, limit=20) for i in range(3)]
    plan(world)
    before = [j.planned_start for j in jobs]
    n_decisions = session.query(Decision).count()
    stats = plan(world, now=NOW + timedelta(seconds=30))
    assert [j.planned_start for j in jobs] == before
    assert stats["deferred"] == 0 and stats["unchanged"] == 3
    assert session.query(Decision).count() == n_decisions


def test_a_job_about_to_start_is_pinned(world):
    session, site, _ = world
    soon = NOW + timedelta(minutes=5)  # inside the 10-minute freeze margin
    j = add_job(session, site, "1", plan_status="planned", planned_start=soon, applied_start=soon)
    plan(world)
    assert j.planned_start == soon and session.query(Decision).count() == 0


def test_running_deferred_jobs_keep_their_capacity(world):
    session, site, _ = world
    add_job(  # 4 GPUs for 60 min from 10:00 = the whole cheap window at 60 GPU-min per block
        session, site, "run", gpus=4, limit=60, state="RUNNING", actual_start=CHEAP_START, applied_start=CHEAP_START,
        plan_status="planned",
    )
    j = add_job(session, site, "2", gpus=4, limit=15)
    plan(world)
    assert j.planned_start is not None
    assert j.planned_start + 15 * MIN <= CHEAP_START or j.planned_start >= CHEAP_END
    assert float(j.planned_cost) < float(j.baseline_cost)  # still a solar-zone hour, just not the cheap IEX one


def test_no_prices_means_unplaceable_and_then_placed_when_prices_arrive(session):
    seed_tod(session)
    seed_catalogue(session)
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)
    tariff = get_tariff(session, "MSEDCL", "HT-I(A)", date(2026, 7, 1))
    j = add_job(session, site, "1")
    stats = planner.plan_site(session, site, tariff, now=NOW, mode="autonomous")
    assert stats["unplaceable"] == 1 and j.plan_status == "unplaceable"
    add_prices(session, NOW, evening_to_next_noon())
    planner.plan_site(session, site, tariff, now=NOW + 10 * MIN, mode="autonomous")
    assert j.plan_status == "planned" and j.planned_start is not None


def test_a_job_bigger_than_the_site_cap_is_unplaceable(world):
    session, site, _ = world
    j = add_job(session, site, "1", gpus=100)
    plan(world)
    assert j.plan_status == "unplaceable" and j.planned_start is None


def test_a_job_without_a_time_limit_is_unplaceable(world):
    session, site, _ = world
    j = add_job(session, site, "1", limit=None)
    plan(world)
    assert j.plan_status == "unplaceable"
    assert session.query(AuditLog).filter_by(event="unplaceable", ref="1").count() == 1


@pytest.mark.parametrize("status", ["skipped", "abandoned", "released"])
def test_jobs_we_stopped_managing_are_never_touched(world, status):
    session, site, _ = world
    j = add_job(session, site, "1", plan_status=status)
    plan(world)
    assert j.plan_status == status and j.planned_start is None and j.baseline_start is None


def test_a_planned_job_keeps_its_window_if_the_prices_disappear(world):
    session, site, tariff = world
    j = add_job(session, site, "1")
    plan(world)
    kept = j.planned_start
    session.query(PriceSignal).delete()
    session.flush()
    stats = plan(world, now=NOW + 10 * MIN)
    assert j.plan_status == "planned" and j.planned_start == kept and stats["unchanged"] == 1
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_planner.py -q`
Expected: FAIL, `ImportError: cannot import name 'planner' from 'app'`.

- [ ] **Step 4: Write the planner**

Create `backend/app/planner.py`:

```python
"""Cloud planner for jobs a site agent reported: pick a start time in the cheapest window, never past the hard limit.
Reuses the demo's allocator (merit order over 15-minute blocks, GPU-weighted capacity) and bill maths."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.allocator import BLOCK_MIN, Block, NoCapacityError, allocate, floor_block
from app.catalogue import Tariff
from app.config import settings
from app.models import Decision, ManagedJob, PriceSignal, Site
from app.savings import bill_cost
from app.service import _book, active_source
from app.tariff import tod_multiplier

MOVABLE = ("pending", "planned", "unplaceable")  # statuses the planner may (re)plan; the rest are hands-off


def plausible_predicted(predicted: datetime | None, first_seen: datetime, max_wait_min: int | None) -> datetime | None:
    """Slurm's predicted start is N/A at first and exactly +1 year when running jobs have no time limit (Spike 0), so
    accept it only if present and no later than first_seen + max_wait."""
    if predicted is None or max_wait_min is None:
        return None
    return predicted if predicted <= first_seen + timedelta(minutes=max_wait_min) else None


def baseline_of(job: ManagedJob) -> datetime:
    """When the job would have started without Wattshift: the plausible prediction, else the moment we first saw it."""
    p = plausible_predicted(job.predicted_start, job.first_seen, job.max_wait_min)
    return max(p, job.first_seen) if p else job.first_seen


def site_cap(site: Site) -> int:
    """GPU-minutes of shifted work allowed per 15-minute block: a share of the site's GPUs, and never more than the
    site's power limit allows. # ponytail: the cloud cannot see the site's other load, so the power limit caps shifted
    load only; add measured site load to cap total demand."""
    cap = float(site.shift_capacity_share) * site.gpus * BLOCK_MIN
    if site.power_limit_kw is not None:
        cap = min(cap, float(site.power_limit_kw) / float(site.kw_per_gpu) * BLOCK_MIN)
    return int(cap)


def _mark_unplaceable(session: Session, site: Site, job: ManagedJob, reason: str, now: datetime, stats: dict) -> None:
    stats["unplaceable"] += 1
    if job.plan_status != "unplaceable":  # audit the change, not every 30 s poll
        job.plan_status, job.note = "unplaceable", reason
        audit.log(session, site.id, now, "planner", "unplaceable", ref=job.ref, reason=reason)


def plan_site(session: Session, site: Site, tariff: Tariff, *, now: datetime, mode: str) -> dict[str, int]:
    """(Re)plan every pending managed job of the site. The caller holds the site's advisory lock and commits.
    # ponytail: naive full re-run each sync with no hysteresis; add a minimum-improvement threshold if jobs churn."""
    margin = timedelta(seconds=site.start_margin_s)
    horizon = now + timedelta(minutes=settings.freeze_minutes)
    cap = site_cap(site)
    kw_per_gpu = float(site.kw_per_gpu)
    stats = {"deferred": 0, "normal": 0, "unchanged": 0, "unplaceable": 0}

    jobs = session.scalars(
        select(ManagedJob)
        .where(ManagedJob.site_id == site.id, ManagedJob.state.in_(("PENDING", "RUNNING")))
        .order_by(ManagedJob.submit_time, ManagedJob.ref)  # submission order: an unchanged world re-plans identically
    ).all()

    ledger: dict[datetime, int] = {}
    movable: list[ManagedJob] = []
    for j in jobs:
        w = max(j.gpus or 1, 1)
        if j.state == "RUNNING":
            if j.applied_start and j.actual_start and j.time_limit_min:  # only work WE shifted uses shifted capacity
                _book(ledger, j.actual_start, j.time_limit_min, w)
        elif j.plan_status in MOVABLE:
            if j.applied_start and j.applied_start <= horizon:  # about to start: pinned, keeps its capacity
                if j.time_limit_min:
                    _book(ledger, j.applied_start, j.time_limit_min, w)
            else:
                movable.append(j)
    if not movable:
        return stats

    windows: dict = {}  # job id -> (baseline, earliest, latest start)
    for j in movable:
        if j.time_limit_min is None or j.max_wait_min is None:
            continue
        base = baseline_of(j)
        j.baseline_start = base
        windows[j.id] = (base, max(now, base), base + timedelta(minutes=j.max_wait_min) - margin)
    last_end = max((windows[j.id][2] + timedelta(minutes=j.time_limit_min) for j in movable if j.id in windows), default=now)
    prices = session.execute(
        select(PriceSignal.ts, PriceSignal.price_rs_per_mwh)
        .where(PriceSignal.source == active_source(), PriceSignal.ts >= floor_block(now), PriceSignal.ts < last_end)
        .order_by(PriceSignal.ts)
    )
    blocks = [Block(ts, float(p) * tod_multiplier(ts, tariff.rules)) for ts, p in prices]

    for j in movable:
        w = max(j.gpus or 1, 1)
        if j.id not in windows:
            _mark_unplaceable(session, site, j, "missing_time_limit_or_max_wait", now, stats)
            continue
        base, earliest, latest = windows[j.id]
        dur, power = j.time_limit_min, w * kw_per_gpu
        try:
            start = allocate(str(j.id), dur, earliest, latest + timedelta(minutes=dur), blocks, ledger, cap, weight=w)
        except NoCapacityError:
            if j.plan_status == "planned" and j.planned_start:  # a bad price refresh must never unschedule a planned job
                _book(ledger, j.planned_start, dur, w)
                stats["unchanged"] += 1
            else:
                _mark_unplaceable(session, site, j, "no_window", now, stats)
            continue

        baseline_cost = bill_cost(base, dur, power, tariff.rules, tariff.base_rate)
        planned_cost = bill_cost(start, dur, power, tariff.rules, tariff.base_rate)
        if planned_cost < baseline_cost:  # defer only for a strictly lower bill
            target = start
        else:  # not worth deferring; if a deferral is already set in Slurm, pull it back to "now"
            target = earliest if j.applied_start else None
            planned_cost = bill_cost(target or base, dur, power, tariff.rules, tariff.base_rate)
        if target is not None:
            _book(ledger, target, dur, w)

        changed = j.plan_status != "planned" or j.planned_start != target
        j.plan_status, j.note = "planned", None
        j.baseline_cost, j.planned_cost = baseline_cost, planned_cost
        if not changed:
            stats["unchanged"] += 1
            continue
        j.planned_start = target
        if target is None:
            stats["normal"] += 1
            continue
        stats["deferred"] += 1
        session.add(Decision(
            managed_job_id=j.id, site_id=site.id, created_at=now, start_at=target, mode=mode,
            baseline_start=base, baseline_cost=baseline_cost, planned_cost=planned_cost,
        ))
        audit.log(session, site.id, now, "planner", "decision", ref=j.ref, start_at=target.isoformat(), mode=mode)
    session.flush()
    return stats
```

- [ ] **Step 5: Run it and confirm it passes**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_planner.py -q`
Expected: all pass. If `test_running_deferred_jobs_keep_their_capacity` fails on the last assertion, the fixture prices leave no solar-zone block outside the cheap window with room; in that case only that assertion is wrong and should be dropped, not the capacity assertion above it.

- [ ] **Step 6: Mutation-check the two safety rules**

Temporarily make each change below, confirm exactly the named test fails, then restore it:
- drop `- margin` from `windows[j.id] = (...)`: `test_never_later_than_baseline_plus_wait_minus_margin` fails (a job is planned at 09:00, later than 08:00).
- change `if planned_cost < baseline_cost:` to `if True:`: `test_runs_as_normal_when_now_is_already_the_cheapest_bill` fails.
- delete the `if target is not None: _book(...)` lines: `test_capacity_spreads_jobs_and_never_overbooks` fails.

---

### Task 5: The sync service

**Files:**
- Create: `backend/app/agent_sync.py`, `backend/tests/test_agent_sync.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 4, `sites.set_release_all`, `audit.log`, `audit.log_throttled`, `catalogue.get_tariff`, `planner.plan_site`, `IST`.
- Produces:
  - Pydantic models `JobFact`, `Applied`, `SyncIn`, `DecisionOut`, `SyncOut` (fields per the contract section at the top of this plan).
  - `agent_sync.process_sync(session, site: Site, body: SyncIn, now: datetime) -> SyncOut`. It takes a per-site advisory lock, does not commit, and is idempotent for a repeated body.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_agent_sync.py`:

```python
from datetime import timedelta

import pytest

from app import agent_sync, sites
from app.catalogue import seed_catalogue
from app.models import AuditLog, Decision, ManagedJob
from app.seed import seed_tod
from tests.helpers import NOW, add_prices, evening_to_next_noon

CHEAP_START = NOW + timedelta(hours=15)
CHEAP_END = NOW + timedelta(hours=16)


@pytest.fixture()
def world(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon())
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8, mode="autonomous")
    return session, site


def fact(ref, **kw):
    base = {"ref": ref, "state": "PENDING", "submit_time": NOW.isoformat(), "gpus": 4, "time_limit_min": 30, "max_wait_min": 17 * 60}
    return {**base, **kw}


def body(*jobs, mode="autonomous", **kw):
    return agent_sync.SyncIn.model_validate(
        {"agent_version": "0.1.0", "mode": mode, "sent_at": NOW.isoformat(), "jobs": list(jobs), **kw}
    )


def sync(world, b, now=NOW):
    session, site = world
    return agent_sync.process_sync(session, site, b, now)


def job(session, ref="1"):
    session.expire_all()
    return session.query(ManagedJob).filter_by(ref=ref).one()


def events(session):
    return [(a.actor, a.event) for a in session.query(AuditLog).order_by(AuditLog.id)]


def test_first_sync_records_the_job_and_returns_a_decision(world):
    session, _ = world
    out = sync(world, body(fact("1")))
    assert [d.ref for d in out.decisions] == ["1"]
    assert CHEAP_START <= out.decisions[0].start_at and out.decisions[0].start_at + timedelta(minutes=30) <= CHEAP_END
    assert out.release_all is False and out.next_poll_s == 30
    j = job(session)
    assert j.plan_status == "planned" and j.gpus == 4 and j.first_seen == NOW and j.baseline_start == NOW
    assert ("agent", "job_seen") in events(session) and ("planner", "decision") in events(session)


def test_shadow_plans_and_logs_but_returns_no_decisions(world):
    session, site = world
    site.mode = "shadow"
    session.flush()  # process_sync re-reads the site row after taking the lock
    out = sync(world, body(fact("1"), mode="shadow"))
    assert out.decisions == []
    d = session.query(Decision).one()
    assert d.mode == "shadow" and float(d.planned_cost) < float(d.baseline_cost)  # the "would have saved" figure


def test_both_sides_must_say_autonomous(world):
    session, _ = world  # site says autonomous, the agent says shadow
    assert sync(world, body(fact("1"), mode="shadow")).decisions == []
    assert ("planner", "mode_mismatch") in events(session)


def test_a_repeated_sync_changes_nothing(world):
    session, _ = world
    b = body(fact("1"), fact("2", gpus=2))
    first = sync(world, b)
    n_dec, n_ev, n_jobs = session.query(Decision).count(), len(events(session)), session.query(ManagedJob).count()
    second = sync(world, b, NOW + timedelta(seconds=30))
    assert second.decisions == first.decisions
    assert (session.query(Decision).count(), len(events(session)), session.query(ManagedJob).count()) == (n_dec, n_ev, n_jobs)


def test_a_confirmed_start_time_is_not_sent_again(world):
    session, _ = world
    start = sync(world, body(fact("1"))).decisions[0].start_at
    out = sync(world, body(fact("1"), applied=[{"ref": "1", "start_at": start.isoformat(), "ok": True, "error": None}]), NOW + timedelta(seconds=30))
    assert out.decisions == [] and job(session).applied_start == start


def test_an_apply_failure_abandons_the_job(world):
    session, _ = world
    start = sync(world, body(fact("1"))).decisions[0].start_at
    b = body(fact("1"), applied=[{"ref": "1", "start_at": start.isoformat(), "ok": False, "error": "Invalid user id"}])
    assert sync(world, b, NOW + timedelta(seconds=30)).decisions == []
    j = job(session)
    assert j.plan_status == "abandoned" and "Invalid user id" in j.note
    assert sync(world, body(fact("1")), NOW + timedelta(seconds=60)).decisions == []  # never retried


def test_a_user_override_stops_management(world):
    session, _ = world
    sync(world, body(fact("1")))
    assert sync(world, body(fact("1", override=True)), NOW + timedelta(seconds=30)).decisions == []
    assert job(session).plan_status == "abandoned" and job(session).note == "user_changed"


def test_skipped_jobs_are_never_planned(world):
    session, _ = world
    assert sync(world, body(fact("1", skipped_reason="array"))).decisions == []
    assert job(session).plan_status == "skipped"


def test_release_all_round_trip(world):
    session, site = world
    start = sync(world, body(fact("1"))).decisions[0].start_at
    sync(world, body(fact("1"), applied=[{"ref": "1", "start_at": start.isoformat(), "ok": True}]), NOW + timedelta(seconds=30))
    sites.set_release_all(session, site, True, NOW + timedelta(seconds=40))
    out = sync(world, body(fact("1"), fact("2")), NOW + timedelta(seconds=60))
    assert out.release_all is True and out.decisions == []  # nothing new is planned while the switch is on
    assert job(session, "2").plan_status == "pending"
    sync(world, body(fact("1"), fact("2"), released=["1"]), NOW + timedelta(seconds=90))
    assert job(session, "1").plan_status == "released"
    sites.set_release_all(session, site, False, NOW + timedelta(seconds=120))
    out = sync(world, body(fact("1"), fact("2")), NOW + timedelta(seconds=150))
    assert [d.ref for d in out.decisions] == ["2"]  # released job stays released; the new one is planned again


def test_the_agent_can_ask_for_release_all(world):
    session, site = world
    out = sync(world, body(fact("1"), release_all=True))
    assert site.release_all is True and out.release_all is True and out.decisions == []


def test_states_never_go_backwards(world):
    session, _ = world
    sync(world, body(fact("1", state="RUNNING", start_time=(NOW + timedelta(minutes=1)).isoformat())))
    sync(world, body(fact("1", state="PENDING")), NOW + timedelta(seconds=30))
    assert job(session).state == "RUNNING"


def test_a_finished_job_records_its_real_times(world):
    session, _ = world
    s, e = NOW + timedelta(hours=1), NOW + timedelta(hours=2)
    sync(world, body(fact("1", state="COMPLETED", start_time=s.isoformat(), end_time=e.isoformat())))
    j = job(session)
    assert (j.state, j.actual_start, j.actual_end) == ("COMPLETED", s, e)


def test_gpus_are_captured_once(world):
    session, _ = world
    sync(world, body(fact("1", gpus=4)))
    sync(world, body(fact("1", gpus=99)), NOW + timedelta(seconds=30))
    assert job(session).gpus == 4


def test_a_late_plausible_prediction_updates_the_baseline_until_a_start_time_is_applied(world):
    session, _ = world
    sync(world, body(fact("1", predicted_start=(NOW + timedelta(days=365)).isoformat())))
    assert job(session).baseline_start == NOW  # bogus year-ahead value ignored
    sync(world, body(fact("1", predicted_start=(NOW + timedelta(hours=2)).isoformat())), NOW + timedelta(seconds=30))
    assert job(session).baseline_start == NOW + timedelta(hours=2)


def test_no_valid_tariff_plans_nothing_but_still_records_jobs(world):
    session, _ = world
    later = NOW.replace(year=2028)
    out = sync(world, body(fact("1")), later)
    assert out.decisions == [] and job(session).plan_status == "pending"
    assert ("planner", "no_tariff") in events(session)


def test_a_shadow_agent_reporting_writes_is_flagged(world):
    session, _ = world
    sync(world, body(fact("1"), mode="shadow", applied=[{"ref": "1", "start_at": NOW.isoformat(), "ok": True}]))
    assert ("agent", "shadow_violation") in events(session)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_agent_sync.py -q`
Expected: FAIL, `ImportError: cannot import name 'agent_sync' from 'app'`.

- [ ] **Step 3: Write the service**

Create `backend/app/agent_sync.py`:

```python
"""POST /agent/v1/sync: the agent reports what it sees, the cloud answers with start-time decisions (spec section 6).
Everything is keyed by (site, ref), so a retried request changes nothing."""
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app import audit, planner, sites
from app.catalogue import get_tariff
from app.models import ManagedJob, Site
from app.tariff import IST

POLL_SECONDS = 30
STATES = ("PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OTHER")
_RANK = {"PENDING": 0, "RUNNING": 1}  # every terminal state ranks 2


class JobFact(BaseModel):
    ref: str = Field(min_length=1, max_length=64)  # the Slurm job id
    state: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OTHER"]
    submit_time: AwareDatetime | None = None
    gpus: int | None = Field(None, ge=0, le=100_000)
    time_limit_min: int | None = Field(None, ge=1, le=525_600)
    max_wait_min: int | None = Field(None, ge=0, le=525_600)
    predicted_start: AwareDatetime | None = None
    start_time: AwareDatetime | None = None
    end_time: AwareDatetime | None = None
    override: bool = False  # the agent saw a change it did not make
    skipped_reason: str | None = Field(None, max_length=64)  # array | dependency | requeue: never planned


class Applied(BaseModel):
    ref: str = Field(min_length=1, max_length=64)
    start_at: AwareDatetime
    ok: bool
    error: str | None = Field(None, max_length=500)


class SyncIn(BaseModel):
    agent_version: str = Field(max_length=32)
    mode: Literal["shadow", "autonomous"]
    sent_at: AwareDatetime  # informational
    jobs: list[JobFact] = Field(default_factory=list, max_length=2000)
    applied: list[Applied] = Field(default_factory=list, max_length=2000)
    released: list[str] = Field(default_factory=list, max_length=2000)  # refs set to start now after release_all
    release_all: bool = False  # the operator ran release-all on the agent


class DecisionOut(BaseModel):
    ref: str
    start_at: AwareDatetime


class SyncOut(BaseModel):
    decisions: list[DecisionOut]
    release_all: bool
    next_poll_s: int


def _rank(state: str) -> int:
    return _RANK.get(state, 2)


def _record_agent(session: Session, site: Site, body: SyncIn, now: datetime) -> str:
    """Note the agent's heartbeat and return the EFFECTIVE mode: autonomous only if site and agent both say so."""
    site.last_seen_at, site.agent_version = now, body.agent_version
    if site.last_agent_mode != body.mode:
        audit.log(session, site.id, now, "agent", "agent_mode", mode=body.mode, previous=site.last_agent_mode)
        site.last_agent_mode = body.mode
    if site.mode != body.mode:
        audit.log_throttled(session, site.id, now, "planner", "mode_mismatch", site_mode=site.mode, agent_mode=body.mode)
    return "autonomous" if site.mode == "autonomous" and body.mode == "autonomous" else "shadow"


def _upsert_jobs(session: Session, site: Site, body: SyncIn, now: datetime, jobs: dict[str, ManagedJob]) -> None:
    for f in body.jobs:
        j = jobs.get(f.ref)
        if j is None:
            j = ManagedJob(
                site_id=site.id, ref=f.ref, state=f.state, first_seen=now, submit_time=f.submit_time or now, gpus=f.gpus,
                time_limit_min=f.time_limit_min, max_wait_min=f.max_wait_min, predicted_start=f.predicted_start,
                actual_start=f.start_time, actual_end=f.end_time,
            )
            session.add(j)
            jobs[f.ref] = j
            audit.log(session, site.id, now, "agent", "job_seen", ref=f.ref, state=f.state)
        else:
            if _rank(f.state) < _rank(j.state):
                continue  # a stale (retried or reordered) report never moves a job backwards
            j.state = f.state
            if j.gpus is None and f.gpus is not None:
                j.gpus = f.gpus  # captured at first sight and never overwritten (sacct may not have GPUs later)
            if f.time_limit_min is not None:
                j.time_limit_min = f.time_limit_min
            if f.max_wait_min is not None:
                j.max_wait_min = f.max_wait_min
            if f.predicted_start is not None and j.state == "PENDING" and j.applied_start is None:
                j.predicted_start = f.predicted_start  # it is N/A at first; the baseline freezes once we defer the job
            if f.start_time:
                j.actual_start = f.start_time
            if f.end_time:
                j.actual_end = f.end_time
        if f.override and j.plan_status != "abandoned":
            j.plan_status, j.note = "abandoned", "user_changed"
            audit.log(session, site.id, now, "agent", "override", ref=f.ref)
        elif f.skipped_reason and j.plan_status in planner.MOVABLE:
            j.plan_status, j.note = "skipped", f.skipped_reason
            audit.log(session, site.id, now, "agent", "skipped", ref=f.ref, reason=f.skipped_reason)


def _apply_reports(session: Session, site: Site, body: SyncIn, now: datetime, jobs: dict[str, ManagedJob]) -> None:
    if body.applied and body.mode == "shadow":  # a shadow agent must have no write path: flag it loudly
        audit.log(session, site.id, now, "agent", "shadow_violation", refs=[a.ref for a in body.applied][:50])
    for a in body.applied:
        j = jobs.get(a.ref)
        if j is None:
            audit.log(session, site.id, now, "agent", "unknown_ref", ref=a.ref)
        elif a.ok:
            if j.applied_start != a.start_at:  # a retried report logs once
                j.applied_start = a.start_at
                audit.log(session, site.id, now, "agent", "applied", ref=a.ref, start_at=a.start_at.isoformat())
        elif j.plan_status != "abandoned":  # the agent could not apply it: leave the job alone for good
            j.plan_status, j.note = "abandoned", f"apply_failed: {a.error or 'unknown'}"[:200]
            audit.log(session, site.id, now, "agent", "apply_failed", ref=a.ref, error=a.error)
    for ref in body.released:
        j = jobs.get(ref)
        if j is not None and j.plan_status != "released":
            j.plan_status, j.note = "released", "release_all"
            audit.log(session, site.id, now, "agent", "released", ref=ref)


def process_sync(session: Session, site: Site, body: SyncIn, now: datetime) -> SyncOut:
    """Apply the agent's report, re-plan the site, return what the agent should do. The caller commits."""
    session.execute(text("select pg_advisory_xact_lock(hashtextextended(:sid, 0))"), {"sid": str(site.id)})
    session.refresh(site)  # take the lock first, then read: another request may have changed the switches
    mode = _record_agent(session, site, body, now)
    if body.release_all and not site.release_all:
        sites.set_release_all(session, site, True, now, actor="agent")

    refs = {f.ref for f in body.jobs} | {a.ref for a in body.applied} | set(body.released)
    jobs = {
        j.ref: j
        for j in session.scalars(
            select(ManagedJob).where(
                ManagedJob.site_id == site.id, or_(ManagedJob.ref.in_(refs), ManagedJob.state.in_(("PENDING", "RUNNING")))
            )
        )
    }
    _upsert_jobs(session, site, body, now, jobs)
    _apply_reports(session, site, body, now, jobs)

    if not site.release_all:
        try:
            tariff = get_tariff(session, site.utility, site.tariff_category, now.astimezone(IST).date())
        except LookupError:
            audit.log_throttled(session, site.id, now, "planner", "no_tariff", utility=site.utility, category=site.tariff_category)
        else:
            planner.plan_site(session, site, tariff, now=now, mode=mode)

    decisions: list[DecisionOut] = []
    if mode == "autonomous" and not site.release_all:
        pending = session.scalars(
            select(ManagedJob)
            .where(
                ManagedJob.site_id == site.id, ManagedJob.state == "PENDING", ManagedJob.plan_status == "planned",
                ManagedJob.planned_start.is_not(None),
            )
            .order_by(ManagedJob.submit_time, ManagedJob.ref)
        )
        decisions = [DecisionOut(ref=j.ref, start_at=j.planned_start) for j in pending if j.planned_start != j.applied_start]
    session.flush()
    return SyncOut(decisions=decisions, release_all=site.release_all, next_poll_s=POLL_SECONDS)
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_agent_sync.py -q`
Expected: all pass. If `test_a_repeated_sync_changes_nothing` fails on the audit count, an event is being logged on every call: find it in the failure diff and make it log on change only.

- [ ] **Step 5: Mutation-check the guarantees**

Confirm each of these makes exactly the named test fail, then restore:
- change `if _rank(f.state) < _rank(j.state): continue` to `pass`: `test_states_never_go_backwards` fails.
- change `mode = "autonomous" if site.mode == "autonomous" and body.mode == "autonomous" else "shadow"` to `mode = body.mode`: `test_both_sides_must_say_autonomous` fails.
- delete the `and not site.release_all` on the decisions line: `test_release_all_round_trip` fails.

---

### Task 6: The HTTP layer

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_agent_api.py`

**Interfaces:**
- Consumes: `agent_sync.SyncIn`, `SyncOut`, `process_sync` (Task 5), `sites.site_for_key`, `sites.set_mode`, `sites.set_release_all` (Task 3), the existing `get_session`, `get_now`, `require_api_key`.
- Produces: `POST /agent/v1/sync` (header `X-Site-Key`; 401 on a missing, unknown or revoked key), `POST /sites/{site_id}/mode` (body `{"mode": "shadow"|"autonomous"}`) and `POST /sites/{site_id}/release-all` (body `{"on": bool}`), both guarded by the existing `X-API-Key`, both returning `{"site_id", "mode", "release_all"}`, 404 for an unknown site.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_agent_api.py`:

```python
import itertools
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main, sites
from app.catalogue import seed_catalogue
from app.seed import seed_tod
from tests.helpers import NOW, add_prices, evening_to_next_noon

CHEAP_START = NOW + timedelta(hours=15)
CHEAP_END = NOW + timedelta(hours=16)


def payload(*jobs, mode="autonomous", **kw):
    return {"agent_version": "0.1.0", "mode": mode, "sent_at": NOW.isoformat(), "jobs": list(jobs), **kw}


def fact(ref, **kw):
    return {"ref": ref, "state": "PENDING", "submit_time": NOW.isoformat(), "gpus": 4, "time_limit_min": 30,
            "max_wait_min": 17 * 60, **kw}


@pytest.fixture()
def env(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon())
    site, key = sites.create_site(session, "Acme", "Pune-1", gpus=8, mode="autonomous")
    ticks = itertools.count()
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: NOW + timedelta(seconds=next(ticks))
    yield TestClient(main.app), site, {"X-Site-Key": key}
    main.app.dependency_overrides.clear()


def test_a_key_is_required(env):
    client, _, _ = env
    assert client.post("/agent/v1/sync", json=payload()).status_code == 401
    assert client.post("/agent/v1/sync", json=payload(), headers={"X-Site-Key": "wsk_wrong"}).status_code == 401


def test_a_revoked_key_is_refused(env, session):
    client, site, headers = env
    sites.revoke_keys(session, site, NOW)
    assert client.post("/agent/v1/sync", json=payload(), headers=headers).status_code == 401


def test_sync_returns_decisions_inside_the_cheap_window(env):
    client, _, headers = env
    r = client.post("/agent/v1/sync", json=payload(fact("1"), fact("2", gpus=2), fact("3", gpus=2)), headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert sorted(d["ref"] for d in out["decisions"]) == ["1", "2", "3"]
    assert out["release_all"] is False and out["next_poll_s"] == 30
    for d in out["decisions"]:
        start = datetime.fromisoformat(d["start_at"])
        assert CHEAP_START <= start <= CHEAP_END


def test_a_retried_request_gets_the_same_answer(env):
    client, _, headers = env
    body = payload(fact("1"))
    a = client.post("/agent/v1/sync", json=body, headers=headers).json()
    b = client.post("/agent/v1/sync", json=body, headers=headers).json()
    assert a["decisions"] == b["decisions"]


@pytest.mark.parametrize(
    "patch",
    [
        {"mode": "yolo"},
        {"sent_at": "2026-07-01T19:00:00"},  # naive time
        {"jobs": [{"ref": "1", "state": "WEIRD"}]},
        {"jobs": [{"ref": "", "state": "PENDING"}]},
        {"jobs": [{"ref": "1", "state": "PENDING", "gpus": -1}]},
    ],
)
def test_bad_input_is_422(env, patch):
    client, _, headers = env
    assert client.post("/agent/v1/sync", json={**payload(), **patch}, headers=headers).status_code == 422


def test_mode_switch_changes_what_the_agent_receives(env):
    client, site, headers = env
    assert client.post(f"/sites/{site.id}/mode", json={"mode": "shadow"}).json()["mode"] == "shadow"
    assert client.post("/agent/v1/sync", json=payload(fact("1")), headers=headers).json()["decisions"] == []
    client.post(f"/sites/{site.id}/mode", json={"mode": "autonomous"})
    assert len(client.post("/agent/v1/sync", json=payload(fact("1")), headers=headers).json()["decisions"]) == 1


def test_release_all_switch_reaches_the_agent(env):
    client, site, headers = env
    r = client.post(f"/sites/{site.id}/release-all", json={"on": True})
    assert r.json() == {"site_id": str(site.id), "mode": "autonomous", "release_all": True}
    out = client.post("/agent/v1/sync", json=payload(fact("1")), headers=headers).json()
    assert out["release_all"] is True and out["decisions"] == []


def test_unknown_site_is_404_and_bad_mode_is_422(env):
    client, site, _ = env
    assert client.post(f"/sites/{uuid.uuid4()}/mode", json={"mode": "shadow"}).status_code == 404
    assert client.post(f"/sites/{site.id}/mode", json={"mode": "yolo"}).status_code == 422
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_agent_api.py -q`
Expected: FAIL (404 on the new routes).

- [ ] **Step 3: Add the endpoints**

In `backend/app/main.py` change the import line `from app import backtest_runs, clock, db, replay, runner, service` to `from app import agent_sync, backtest_runs, clock, db, replay, runner, service, sites` and change `from app.models import Job, SavingsLog` to `from app.models import Job, SavingsLog, Site`.

Append after the `/tariff` endpoint at the end of the file:

```python
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
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_agent_api.py -q`
Expected: all pass.

---

### Task 7: Regression, spec update and a live check

**Files:**
- Modify: `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md`

- [ ] **Step 1: Run the whole backend suite**

Run: `cd backend; .\.venv\Scripts\python -m pytest -q`
Expected: all green: the previous 176 plus the new tests. If any old test fails, the cause is Task 4's `_book` change or Task 1's models import; fix the cause, do not edit the old test.

- [ ] **Step 2: Write the contract additions into the spec**

In section 6 of the spec, extend the request JSON example and add a paragraph. In the request, each job may also carry `"override": false` and `"skipped_reason": null`; the request may also carry `"released": []` and `"release_all": false`. Add below the response example:

> **Extra fields.** `override` (bool): the agent saw a change it did not make, so the cloud stops managing the job. `skipped_reason` (`array | dependency | requeue`): the job is never planned. `released` (refs): jobs the agent set to start now after `release_all`. Request `release_all` (bool): the operator ran `release-all` on the agent, so the cloud turns the site's kill switch on; the response `release_all` stays true until an operator turns it off, and while it is true the cloud plans nothing. `state` is one of `PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT, OTHER`. The effective mode is autonomous only if the site setting and the agent's `mode` both say so; otherwise decisions are recorded but not returned.

In section 7 add two bullets: "**Defer only for a strictly lower bill.** A job whose planned start does not bill strictly less than its baseline start is left to Slurm (no decision)." and "**Capacity.** Per block, shifted GPU-minutes are capped at `shift_capacity_share x GPUs x 15`, and at `power_limit_kw / kw_per_gpu x 15` when the site has a power limit."

In section 12 add: "`managed_jobs.plan_status` is one of `pending, planned, unplaceable, skipped, abandoned, released`; `sites` also carries `shift_capacity_share`, `start_margin_s` (default 120), `release_all` and `last_seen_at`."

In section 15 mark step 2 "**Cloud core: DONE 2026-09-20** (see `docs/superpowers/plans/2026-09-20-cloud-core.md`)".

- [ ] **Step 3: Live check against the dev database**

Onboard a throw-away site and post recorded job facts to the running API (start it as before, `SCHEDULER` unset, `DEMO_MODE=1`):

```powershell
cd backend
$env:PYTHONPATH=(Get-Location).Path
.\.venv\Scripts\python -m scripts.init_db                      # tables, tariff catalogue, today's prices
.\.venv\Scripts\python -m scripts.onboard_site --company Demo --site Lab --gpus 64 --mode autonomous
```

Copy the printed key, then (with the API running on port 8000):

```powershell
$k = "<the key>"
$b = @{agent_version="0.1.0"; mode="autonomous"; sent_at=(Get-Date).ToUniversalTime().ToString("o");
  jobs=@(@{ref="9001"; state="PENDING"; gpus=8; time_limit_min=120; max_wait_min=1440})} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://localhost:8000/agent/v1/sync -Headers @{"X-Site-Key"=$k} -ContentType "application/json" -Body $b
```

Expected: a `decisions` entry for `9001` with a `start_at` in a cheap solar window, or an empty list if the remaining prices today contain no cheaper bill than now (tomorrow's prices are published around midday). Either is correct behaviour; report which one happened and why.

- [ ] **Step 4: Report**

Tell the user, in plain words: what the cloud can now do without any agent, the test count, the live-check result, and that the next plan is the agent (Slurm reader, rules, sync loop, applier, release-all, shadow and autonomous). Do not commit.

---

## Self-review

**Spec coverage.** Section 6 contract (Tasks 5, 6, plus the four additions in Task 7); section 7 hard limit, margin, capacity, prices, "no window means run as normal" (Task 4); section 8 rows: no decision leaves the job alone, apply failure, user change, arrays and skipped (`skipped_reason`), release_all, audit log (Tasks 4, 5); section 9 shadow versus autonomous and the "shadow agent wrote something" alarm (Task 5); section 11 catalogue with expiry (Task 2); section 12 tables (Task 1). Agent-side items (the reader, rules, applier, command allow-list) are in the next plan. Savings from real start times (`actual_cost`, `saved`) are the measurement plan; the columns exist, the shadow "would have saved" figure (`planned_cost` vs `baseline_cost`) is already recorded.

**Placeholders.** None. Two steps deliberately tell the implementer to tighten a test written earlier in the same task (Tasks 2, 4, 6) and give the replacement code.

**Type consistency.** `plan_site(session, site, tariff, *, now, mode)` is defined in Task 4 and called with those keywords in Tasks 4 and 5. `process_sync(session, site, body, now)` is called the same way in Tasks 5 and 6. `Tariff` fields (`rules`, `base_rate`) match their uses in the planner. Stats keys (`deferred`, `normal`, `unchanged`, `unplaceable`) match the tests. `DecisionOut` (schema) is distinct from `Decision` (model).

**Known limits, stated once.** No test for two simultaneous syncs from one site (the per-site advisory lock is the same pattern as the demo's, which is mutation-checked); no DB-level append-only enforcement; the planner re-runs in full on every sync (fine for hundreds of pending jobs per site).
