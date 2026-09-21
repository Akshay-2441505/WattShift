# Tech Stack — Wattshift v1

## Guiding constraint

**No paid services.** This ruled out RunPod and Vast.ai (both genuinely paid GPU rental) and Modal (advertises a free tier but requires a card on file before it unlocks). Every choice below is either free with no card, or already in use for Dekho (so its card/billing status is already known and cleared).

## Backend

- **FastAPI (Python).** API layer + home for the scheduling logic. Python's ecosystem makes the IEX scraping and price-curve math straightforward.
- **APScheduler with a Postgres-backed (SQLAlchemy) job store**, not Celery+Redis. Jobs scheduled hours ahead must survive a restart — the persistent job store handles that without adding a second infra dependency a solo project doesn't need yet. Celery is the documented upgrade path if this ever runs at real scale (note in code comments, don't build now).

## Database

- **Postgres via Neon** — free tier, confirmed no credit card required, and it's what Dekho already runs on.

## Compute execution (the piece that changed from the original plan)

- **Primary: Kaggle API.** Free GPU (~30 hrs/week), no credit card — just phone verification. Our scheduler calls the Kaggle API to push/run a kernel at the time *it* decides, following the pattern used by existing "free GPU CI/CD via Kaggle" tooling. Known rough edges to plan for: per-kernel duration limits, containerized environment restricts some system-level operations, and status-check quirks with certain kernel title formats — test this integration early, before other work depends on it.
- **Fallback: GitHub Actions (`workflow_dispatch`).** 100% free compute minutes, no card, triggered via API at a chosen time. Runs a lightweight stand-in job rather than a real GPU workload — used only if Kaggle proves too unreliable for a given demo run, and always labeled honestly as a budget substitution, not passed off as a GPU job.
- Both sit behind a single `ComputeProvider` interface (see `SYSTEM_DESIGN.md` §2) so the scheduling logic doesn't care which one actually executes.

## Data sources

- **IEX day-ahead market** — public, free, but no clean documented API; scrape/export the published 15-minute block data.
- **MSEDCL ToD tariff** — static config seeded from the published tariff order (verify the exact order before finalizing numbers — see `SYSTEM_DESIGN.md` §5), not a live feed.
- *(Deferred, not v1):* India Energy Atlas (energymap.in) for carbon intensity — promising live per-state feed, API access unconfirmed; worth revisiting once the price-side pipeline is solid.

## Frontend

- **React + Vite + TypeScript**, matching Dekho and Frontage — no new framework to learn while also validating a business idea.
- **Tailwind CSS** for styling, built against the token system in `DESIGN_BRIEF.md`.
- A charting library (Recharts or similar) for the price/carbon forecast and savings charts — pull in a data-viz-specific pass when actually building these, don't improvise chart styling ad hoc.

## Hosting

- **Vercel** (frontend) + **Render** (backend) + **Neon** (Postgres) — same pattern as Dekho. Render's free-tier card requirement was unclear across sources when checked (conflicting info); since Dekho already runs there, confirm what your existing account required rather than assuming either way.

## Explicitly not used, and why

| Considered | Why not |
|---|---|
| RunPod | Paid, no free tier |
| Vast.ai | Paid, no free tier |
| Modal | "Free tier" requires a card on file |
| Celery + Redis | Real upgrade path, but unnecessary infra for v1's scale |
| Electricity Maps API | Only a 14-day trial for India zones, then paywalled |
