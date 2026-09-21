import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    DDL, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Numeric, SmallInteger, Text, UniqueConstraint, event, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base

TS = DateTime(timezone=True)
MONEY = Numeric(14, 4)


class TodScheduleRow(Base):
    """MSEDCL ToD tariff rules — seeded config, never scraped."""

    __tablename__ = "tod_schedule"
    id: Mapped[int] = mapped_column(primary_key=True)
    zone_name: Mapped[str] = mapped_column(Text)  # baseline | solar | peak
    start_hour: Mapped[int] = mapped_column(SmallInteger)  # IST, inclusive
    end_hour: Mapped[int] = mapped_column(SmallInteger)  # IST, exclusive (24 = midnight)
    season: Mapped[str | None] = mapped_column(Text)  # apr_sep | oct_mar | NULL
    rate_adjustment_pct: Mapped[Decimal] = mapped_column(Numeric)


class PriceSignal(Base):
    """IEX day-ahead price for one 15-min block."""

    __tablename__ = "price_signals"
    __table_args__ = (UniqueConstraint("ts", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TS)
    price_rs_per_mwh: Mapped[Decimal] = mapped_column(Numeric)
    source: Mapped[str] = mapped_column(Text, default="iex_dam")  # iex_dam | iex_dam_replay
    ingested_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="jobs_duration_positive"),
        CheckConstraint("provider in ('kaggle','github_actions')", name="jobs_provider_valid"),
        CheckConstraint(
            "status in ('queued','scheduled','running','done','failed')", name="jobs_status_valid"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    duration_minutes: Mapped[int]
    deadline: Mapped[datetime] = mapped_column(TS)
    provider: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued", server_default="queued")
    power_kw: Mapped[Decimal] = mapped_column(Numeric, default=lambda: Decimal(str(settings.default_power_kw)))
    assigned_window_start: Mapped[datetime | None] = mapped_column(TS)
    baseline_cost_rs: Mapped[Decimal | None] = mapped_column(MONEY)  # cost if run at submission time
    planned_cost_rs: Mapped[Decimal | None] = mapped_column(MONEY)  # cost at assigned window
    external_ref: Mapped[str | None] = mapped_column(Text)  # provider's handle (kernel slug / run id)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    executed_at: Mapped[datetime | None] = mapped_column(TS)


class SavingsLog(Base):
    """One row per executed job."""

    __tablename__ = "savings_log"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    baseline_cost_rs: Mapped[Decimal] = mapped_column(MONEY)
    actual_cost_rs: Mapped[Decimal] = mapped_column(MONEY)
    saved_rs: Mapped[Decimal] = mapped_column(MONEY)  # may be negative — never clamped
    computed_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class JobRun(Base):
    """One real run of a job on the GPU, with the power it was measured drawing. Each Kaggle job has two: 'without'
    (a twin started at submission, i.e. what running it right away would have cost) and 'with' (the run at the
    window Wattshift chose). Each is priced at the tariff of the hour it really started (see measured.py)."""

    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "kind"),
        CheckConstraint("kind in ('without','with')", name="job_runs_kind_valid"),
        CheckConstraint("status in ('scheduled','running','done','failed')", name="job_runs_status_valid"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="scheduled")
    external_ref: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # True when the app's clock was the replay clock: the power is still measured, but the hour it was priced at is replayed.
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # From the kernel's own nvidia-smi samples; all null when the reading could not be read back.
    avg_watts: Mapped[Decimal | None] = mapped_column(Numeric)
    peak_watts: Mapped[Decimal | None] = mapped_column(Numeric)
    energy_wh: Mapped[Decimal | None] = mapped_column(Numeric)
    samples: Mapped[int | None]
    gpu_model: Mapped[str | None] = mapped_column(Text)
    power_limit_w: Mapped[Decimal | None] = mapped_column(Numeric)


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
    mode: Mapped[str] = mapped_column(Text)  # shadow | autonomous (the effective mode at the time)
    baseline_start: Mapped[datetime | None] = mapped_column(TS)
    baseline_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    planned_cost: Mapped[Decimal | None] = mapped_column(MONEY)


class AuditLog(Base):
    """Append-only trail of everything that matters: who did what to which job and when. A database trigger (below)
    refuses UPDATE and DELETE, so a bug or a stolen application login cannot rewrite history."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    at: Mapped[datetime] = mapped_column(TS)
    actor: Mapped[str] = mapped_column(Text)  # agent | planner | operator
    event: Mapped[str] = mapped_column(Text)
    ref: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)


AUDIT_GUARD = (
    DDL("create or replace function audit_log_is_append_only() returns trigger language plpgsql as "
        "$$ begin raise exception 'audit_log is append-only'; end $$"),
    DDL("drop trigger if exists audit_log_append_only on audit_log"),
    DDL("create trigger audit_log_append_only before update or delete on audit_log "
        "for each row execute function audit_log_is_append_only()"),
)
for _ddl in AUDIT_GUARD:  # applied whenever the table is created; init_db.py applies them to a table that already exists
    event.listen(AuditLog.__table__, "after_create", _ddl.execute_if(dialect="postgresql"))
