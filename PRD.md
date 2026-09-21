# PRD — Wattshift v1

## 1. Goal

Prove, with a real (not mocked) end-to-end system, that non-urgent compute jobs can be automatically timed to cheaper/cleaner grid windows using India's real ToD tariff structure — and show the ₹ saved.

**Non-goal for v1:** signing a real customer, billing, multi-region support, or handling adversarial/malicious job submissions. This is a demo-grade proof of the mechanism, built to real-world standards where it matters (real data, real API calls, real algorithmic logic) and honestly scoped where it doesn't (one DISCOM, one demo compute account).

## 2. Who this is for (ICP)

India-based neocloud / GPU cloud operators — companies with their own GPU fleet and job queue, where electricity is a large enough share of opex (typically 60–80% at high utilization) that a 2–5% shift is real money. Not individual developers (savings too small to matter) and not hyperscalers (inaccessible as a first mover). See the business doc for full reasoning.

## 3. Job to be done

> When our GPU fleet is running non-urgent, deadline-flexible jobs, we want those jobs automatically timed to our cheapest and cleanest power windows, so we can cut our single largest operating cost without adding headcount or manual monitoring.

## 4. User stories (v1)

1. **As an operator, I submit a job with a deadline** (e.g., "must finish by 8am tomorrow") and the system decides when to actually run it, without me tracking prices myself.
2. **As an operator, I see the current and forecast price/carbon curve** for the next 24h, so I understand *why* the system made a given decision.
3. **As an operator, I see my job queue** — what's waiting, what's running, what's done — and can see each job's assigned window before it fires.
4. **As an operator, I see cumulative ₹ saved** versus a "ran immediately" baseline, so the value is visible, not just claimed.
5. **As the system, when a price/tariff window is at capacity, I route new jobs to the next-best window** rather than piling everything into one slot (the anti-herd requirement — see `SYSTEM_DESIGN.md` §4).

## 5. Functional requirements

| # | Requirement | Priority |
|---|---|---|
| F1 | Ingest MSEDCL ToD tariff schedule (solar/normal/peak hour blocks + rates) as configuration | Must |
| F2 | Ingest IEX day-ahead price data on a schedule (daily) | Must |
| F3 | Accept a job submission: `duration`, `deadline`, `job_type` (real Kaggle kernel vs. GitHub Actions stand-in) | Must |
| F4 | Compute the optimal window for a job: cheapest/cleanest slot before its deadline, respecting the per-window capacity cap | Must |
| F5 | Persist scheduled jobs so they survive a process restart | Must |
| F6 | Fire the job automatically at its assigned time via the Kaggle API (or GitHub Actions fallback) | Must |
| F7 | Log actual vs. baseline ("ran now") cost for every executed job | Must |
| F8 | Dashboard: price/carbon forecast chart, job queue, cumulative savings | Must |
| F9 | Re-evaluate not-yet-run jobs on a fixed interval against fresh price data | Should |
| F10 | Multi-DISCOM tariff support | Won't (v1) |
| F11 | Real payment/commission billing | Won't (v1) |

## 6. Success criteria for v1

- A job submitted with a deadline is genuinely held and fires automatically at a different, non-immediate time, with zero manual intervention.
- The dashboard shows a real, non-zero ₹-saved figure computed against a real ToD rate difference, not a hardcoded/fake number.
- At least one demo run where two-plus jobs land in different windows because of the capacity cap (proves F4/anti-herd logic isn't decorative).

## 7. Phased build order

1. **Data layer** — ToD config + IEX ingestion, stored in Postgres.
2. **Scheduling engine** — window selection + capacity ledger (F4), persisted jobs (F5).
3. **Execution layer** — Kaggle/GitHub Actions integration (F6).
4. **Savings logging** — F7.
5. **Dashboard** — F8, built from `DESIGN_BRIEF.md`.
6. **Re-forecast loop** — F9, only after 1–5 are solid.

This mirrors the roadmap in the business concept doc (Phase 1: fleet-level scheduler) — this PRD is the detailed build plan for that phase specifically, not phases 2–3 (aggregation, grid-facing resource), which are post-v1.
