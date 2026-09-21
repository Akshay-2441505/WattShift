# System Design — Wattshift v1

## 1. Architecture overview

```
 ┌────────────────┐     daily      ┌──────────────────┐
 │  IEX day-ahead  │───scrape/────▶│                   │
 │  price data     │   export      │                   │
 └────────────────┘                │   Postgres        │
 ┌────────────────┐                │  (price_signals,  │
 │  MSEDCL ToD     │───config────▶ │   tod_schedule,    │
 │  tariff rules   │                │   jobs, savings)  │
 └────────────────┘                └─────────┬─────────┘
                                              │
                                    ┌─────────▼─────────┐
                                    │  Scheduling Engine │
                                    │  (FastAPI + APS-   │
                                    │  cheduler)         │
                                    │  - window selection│
                                    │  - capacity ledger │
                                    │  - re-forecast loop│
                                    └─────────┬─────────┘
                                              │ fires job at chosen time
                                    ┌─────────▼─────────┐
                                    │  Execution Layer   │
                                    │  Kaggle API /       │
                                    │  GitHub Actions     │
                                    │  (workflow_dispatch)│
                                    └─────────┬─────────┘
                                              │ result + cost log
                                    ┌─────────▼─────────┐
                                    │  React/Vite         │
                                    │  Dashboard          │
                                    └────────────────────┘
```

## 2. Components

- **Ingestion jobs** (scheduled, APScheduler): pull IEX day-ahead data (scrape/export — no clean documented API, budget real parsing effort); load MSEDCL ToD config (static, versioned in code/config, not scraped).
- **Scheduling engine**: the core logic. Given a job (duration, deadline), evaluates all valid windows before the deadline, picks the best one under the capacity-ledger constraint (§4), persists the decision.
- **Execution layer**: a thin, swappable interface (`ComputeProvider`) with two implementations — `KaggleProvider` (primary) and `GitHubActionsProvider` (fallback/CPU stand-in). Scheduling logic never talks to Kaggle/GitHub directly — only through this interface, so the provider can change without touching the scheduler.
- **Dashboard**: reads from the API layer; no direct DB access from the frontend.

## 3. Data model (Postgres)

```sql
-- MSEDCL ToD tariff rules (static config, seeded not scraped)
CREATE TABLE tod_schedule (
  id SERIAL PRIMARY KEY,
  zone_name TEXT NOT NULL,        -- 'baseline' | 'solar' | 'peak'
  start_hour SMALLINT NOT NULL,   -- 0-23
  end_hour SMALLINT NOT NULL,
  season TEXT,                    -- 'apr_sep' | 'oct_mar' | null (year-round)
  rate_adjustment_pct NUMERIC     -- e.g. -25, -15, +20
);

-- IEX day-ahead price, ingested daily
CREATE TABLE price_signals (
  id SERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,        -- 15-min block start
  price_rs_per_mwh NUMERIC NOT NULL,
  source TEXT DEFAULT 'iex_dam',
  ingested_at TIMESTAMPTZ DEFAULT now()
);

-- Submitted jobs
CREATE TABLE jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  duration_minutes INT NOT NULL,
  deadline TIMESTAMPTZ NOT NULL,
  provider TEXT NOT NULL,         -- 'kaggle' | 'github_actions'
  status TEXT NOT NULL DEFAULT 'queued', -- queued|scheduled|running|done|failed
  assigned_window_start TIMESTAMPTZ,
  submitted_at TIMESTAMPTZ DEFAULT now(),
  executed_at TIMESTAMPTZ
);

-- Savings ledger — one row per executed job
CREATE TABLE savings_log (
  job_id UUID REFERENCES jobs(id),
  baseline_cost_rs NUMERIC,       -- cost if run immediately at submission time
  actual_cost_rs NUMERIC,         -- cost at the window it actually ran in
  saved_rs NUMERIC,
  computed_at TIMESTAMPTZ DEFAULT now()
);
```

## 4. The scheduling algorithm (anti-herd)

Three layers, per the earlier design discussion. v1 builds layers 1–2 for real; layer 3 is stubbed as a re-run of the same function on a timer.

**Layer 1 — windowed, jittered placement.** A job isn't pinned to one instant. It's assigned a band (e.g., the N cheapest hours before its deadline), then its exact start time within that band is set by `hash(job_id) % band_width_minutes` — deterministic, reproducible for demos, not true randomness.

**Layer 2 — capacity-ledger allocation (the real anti-herd fix).** Pseudocode:

```python
def allocate(job, price_curve, capacity_ledger, cap_per_block):
    # price_curve: list of (window_start, effective_rate) before job.deadline
    # effective_rate = IEX price adjusted by ToD zone multiplier
    candidates = sorted(price_curve, key=lambda w: w.effective_rate)
    for window in candidates:
        allocated = capacity_ledger.get(window.start, 0)
        if allocated + job.duration_minutes <= cap_per_block:
            capacity_ledger[window.start] = allocated + job.duration_minutes
            return window.start
    # every window capped out before deadline — hard fail, surface to operator
    raise NoCapacityError(job)
```

This is a merit-order dispatch curve (the same principle utilities use to dispatch power plants cheapest-first) applied to compute jobs instead of generators — fill the cheapest block up to its cap, spill overflow to the next-cheapest.

**Layer 3 — live re-forecast (stub for v1).** A scheduled task re-runs `allocate()` for every job still in `queued`/`scheduled` status against the latest `price_signals` row, in case a block filled up faster than expected. In v1 this can be a naive full re-run on a timer; it does not need to be efficient yet.

## 5. MSEDCL ToD tariff model (Maharashtra)

**Verified 2026-09-19 against the primary MERC orders** (HT Industrial; ToD is a percentage of the *energy charge* only). Source of truth in code: `backend/app/seed.py` and `backend/app/config.py`, pinned by `tests/test_models.py`.

| Zone | Hours | Adjustment | Source |
|---|---|---|---|
| Baseline | 12:00 AM – 9:00 AM | 0% (the −10% night rebate for 00:00–06:00 in the 28 Mar 2025 order was **removed** by the review order, s.18.13) | Case 75 of 2025, 25 Mar 2026, Table 8 (pp.76–77) |
| Solar / off-peak | 9:00 AM – 5:00 PM | −15% (Apr–Sep) / −25% (Oct–Mar), FY 2025-26 and FY 2026-27 | same |
| Peak | 5:00 PM – 12:00 AM | **+25%** for LT/HT Industrial & Commercial (+20% for other categories) | Case 217 of 2024 press note, 28 Mar 2025 |

**Base rate:** HT Industry (I-A) energy charge **₹8.44/kVAh** for FY 2026-27, effective 1 Apr 2026 (Case 75 of 2025, p.100). Wheeling (₹0.81) and fixed charge (₹650/kVA/month) do not vary by time, so they are excluded from savings. It is per kVAh, which equals kWh only at unity power factor.

**Known uncertainty:** Table 8 of the 25 Mar 2026 order prints the peak row as `+20%#` and the `#` footnote text is missing from the PDF text layer; the +25% Industrial rate rests on the earlier order, which the review did not otherwise touch. Verify against the original 827-page order (Case 217 of 2024) or MSEDCL's tariff booklet if the exact peak figure matters.

**Expires 1 April 2027:** MERC steps the numbers each year. FY 2027-28: solar −20% (Apr–Sep) / −30% (Oct–Mar), HT I(A) energy charge ₹8.23/kVAh. `/tariff` reports `verified: false` after `tariff_valid_until` (31 Mar 2027).

Modeled as the `tod_schedule` table above so the multiplier logic doesn't hardcode hours in application code.

## 6. API contract (v1)

```
POST   /jobs                 submit a job {duration_minutes, deadline, provider}
GET    /jobs                 list jobs with status + assigned window
GET    /jobs/{id}             single job detail incl. savings once executed
GET    /forecast              next-24h price + carbon + ToD zone curve
GET    /savings/summary       cumulative ₹ saved, today/week/total
```

## 7. Sequence: job lifecycle

1. Operator (or demo script) `POST /jobs` with duration + deadline.
2. Scheduling engine calls `allocate()` against current `price_signals` + `tod_schedule` + `capacity_ledger`.
3. Job persisted with `status=scheduled`, `assigned_window_start`.
4. APScheduler fires at `assigned_window_start` → calls the `ComputeProvider` interface → Kaggle or GitHub Actions executes.
5. On completion, `savings_log` row written (baseline cost computed retroactively from what the price would've been at submission time).
6. Dashboard reflects updated status + savings on next poll.
