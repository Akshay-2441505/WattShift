# Wattshift — Project Spec (Source of Truth)

> **Status:** Concept validated through business research; pre-build.
> **Working name:** "Wattshift" — placeholder, not finalized. Rename freely; update this file if you do.
> **Purpose of this file:** the single entry point for Claude Code (or any collaborator) building this project. Read this first. It links out to the other docs, which go deeper on each area.
> **Companion docs in this bundle:** `PRD.md`, `SYSTEM_DESIGN.md`, `TECH_STACK.md`, `DESIGN_BRIEF.md`
> **Full business case (market sizing, competitive landscape, business model):** see the published concept doc — https://claude.ai/artifact/HKnepyEaASuRr2S5NsgJeG

---

## 1. The problem, in one paragraph

India's GPU cloud providers ("neoclouds" — Yotta, E2E Networks, NxtGen, NeevCloud) run large GPU fleets where, at high utilization, electricity is 59–77% of operating cost. Almost none of them time *when* they run flexible (non-urgent) workloads. India mandates Time-of-Day (ToD) electricity tariffs for all commercial/industrial consumers ≥10kW — a real, regulated price difference (roughly 10–25% cheaper in solar hours, 10–20% more expensive at evening peak) that these companies are already being billed on, whether or not they act on it.

## 2. The job to be done

> When our GPU fleet is running non-urgent, deadline-flexible jobs, we want those jobs **automatically** timed to our cheapest and cleanest power windows, so we can cut our single largest operating cost without adding headcount or manual monitoring.

Two hard requirements this implies, both non-negotiable in the design:
- **It executes, it does not just notify.** A tool that tells a human "now is cheap" gets learned and abandoned within weeks. The product holds and fires jobs itself.
- **It does not create its own peak.** Naively pointing every job at the single cheapest hour causes a "rebound peak." The scheduler spreads and re-evaluates (see `SYSTEM_DESIGN.md` §4).

## 3. Who it's for

**Primary customer:** India's mid-market neocloud / GPU cloud operators — not individual developers, not hyperscalers. See `PRD.md` §2 for the full ICP.

## 4. What we're actually building for v1 (the honest scope)

We have no real neocloud as a design partner yet. So v1 is a **genuinely real, not mocked, self-contained demo**: real Indian grid price data in, a real scheduling decision, a real job fired via a real API — running against an account we control, not a live customer's fleet. Same pattern as the Frontage buildathon project (real Razorpay test-mode calls, simulated merchant).

Concretely:
- **Price/tariff data:** scraped from IEX (day-ahead market) + modeled against MSEDCL's (Maharashtra) published ToD tariff — real numbers, not synthetic. See `SYSTEM_DESIGN.md` §5.
- **Job execution:** real API calls to **Kaggle** (primary — free GPU, no credit card) and/or **GitHub Actions** (fallback — free CPU, proves the same trigger mechanism). Not RunPod/Vast.ai/Modal — all three require payment or a card on file, which is off the table for this build. See `TECH_STACK.md` for the full reasoning.
- **Dashboard:** shows the job queue, the price/carbon forecast, and cumulative ₹ saved. See `DESIGN_BRIEF.md`.

## 5. What's explicitly out of scope for v1

- Multi-DISCOM / multi-state tariff support (Maharashtra/MSEDCL only — see `SYSTEM_DESIGN.md` §5)
- Real utility/DISCOM integration or grid-facing data submission (that's the Phase 3 vision in the business doc, not a v1 feature)
- A real paying customer or commission billing flow
- Full production-grade anti-herd sophistication (v1 builds the real capacity-ledger allocator; the live re-forecast loop is stubbed — see `SYSTEM_DESIGN.md` §4)

## 6. Open items to verify before/while building

- ~~MSEDCL ToD tariff numbers unverified~~ — **done 2026-09-19** against MERC Case No. 75 of 2025 (25 Mar 2026); see `SYSTEM_DESIGN.md` §5. Corrections: peak is +25% for HT Industrial (not +20%), the energy charge is ₹8.44/kVAh, and there is no night rebate. One residual uncertainty (the peak footnote) is recorded there. The numbers expire 1 April 2027.
- Kaggle's phone-verification and per-kernel duration limits haven't been tested against this specific workflow yet — validate early, before the rest of the build depends on it.
- No real customer conversations have happened. Every savings number is from published third-party research, not a validated figure against a real neocloud's actual bill.

## 7. Reading order for Claude Code

1. This file (context + constraints)
2. `PRD.md` (what to build, in what order)
3. `SYSTEM_DESIGN.md` (how it's built — architecture, data model, API, algorithm)
4. `TECH_STACK.md` (exact tools/libraries, and why)
5. `DESIGN_BRIEF.md` (what it should look like)
