# Business Case — Wattshift

> Markdown export of the published concept doc (https://claude.ai/artifact/HKnepyEaASuRr2S5NsgJeG) for local use alongside the other project docs. The artifact link is the canonical, nicer-formatted version if you want to share it with a person; this file is what Claude Code should read.

## The pitch

"We help India's GPU cloud providers cut electricity — often 60–80% of their operating cost at scale — by automatically timing deferrable AI jobs to the cheapest, cleanest windows the grid already prices differently, using signals most of them aren't using yet."

## 1. The problem

Global data-center electricity demand is projected to hit 565 TWh in 2026 (+26% on 2025), passing 1,200 TWh by 2030. AI-optimized servers are 31% of that draw in 2026, overtaking conventional servers' share by 2027. Gartner: "AI capacity is now constrained by power availability," not chips.

That demand lands on the companies running the GPUs. As utilization rises:
- 20–45% of opex is power, traditional cloud @ 50–60% utilization
- **59–77% of opex is power, AI workloads @ 90% utilization**
- ~80% of opex is power, at 500MW hyperscale

Almost none of them time *when* they draw that power.

## 2. Why now — the opportunity

- Global demand-response market: $11.1B (2025) → $26.6B (2035), 9.1%/yr
- India's growth rate: 15.8%/yr — fastest of any country in the market
- India's neocloud segment: 82% 5-yr revenue CAGR since 2021
- India is rolling out 250M smart meters — the data backbone this needs

India's neoclouds (Yotta, E2E Networks, NxtGen, NeevCloud) are projected to handle 25–35% of India's AI compute by 2030, undercutting hyperscalers by 20–30% on price.

## 3. Who we sell to

Mid-market GPU cloud / neocloud operators — not individual developers (savings too small), not hyperscalers (inaccessible as a first mover).

**JTBD:** When our GPU fleet is running non-urgent, deadline-flexible jobs, we want those jobs automatically timed to our cheapest and cleanest power windows, so we can cut our single largest operating cost without adding headcount or manual monitoring.

## 4. Competitive landscape

| Player | What they do | Who they serve |
|---|---|---|
| Emerald AI | Pauses/shifts/relocates workloads for grid flexibility | Hyperscalers, utilities, regulators |
| GridCARE | Finds hidden grid capacity via predictive analytics | Data center developers, utility planners |
| Camus Energy / GridUnity | Coordinates large loads with utility grid operations | Utility operators, regulators |
| GridBeyond | Real-time ML-driven consumption adjustment | Hyperscale data center operators |
| GridWise AI | Carbon-aware job scheduling vs. deadline | Enterprise batch/AI teams (US/EU) |
| **Wattshift** | Cost + carbon-aware scheduling, India-tariff aware, embedded in fleet queues | India's mid-market neocloud operators |

None of the funded players are built around India's specific pricing mechanism or aimed at the neocloud tier. That's the wedge — an underserved segment, not a new mechanism.

## 5. The solution

Not a notification tool — an execution layer inside a provider's job queue.

- **It executes, it doesn't just notify.** A tool that only informs gets memorized and abandoned within weeks.
- **It spreads recommendations across a window, not one instant** — avoids the "rebound peak" failure mode documented in demand-response research.
- **It's tuned to India's actual price signal.** ToD tariffs are mandatory for all commercial/industrial consumers ≥10kW from April 2024 — a real, regulated price difference on the actual bill (for MSEDCL HT Industrial in FY 2026-27: 15% cheaper in solar hours Apr–Sep, 25% cheaper Oct–Mar, and 25% more expensive at evening peak; verified against MERC Case No. 75 of 2025 — see `SYSTEM_DESIGN.md` §5). India's solar-driven "duck curve" means the cheap window is increasingly midday, not 2am.

## 6. Why the savings are real

ToD pricing is metered and mandatory — the same kWh, run at 1pm vs. 7pm, is billed a genuinely different amount. Independent (MIT-affiliated) research found workload timing flexibility delivers **2–5% total cost reduction** — modest but real and recurring on the biggest opex line a neocloud has.

Distinct from this: switching *where* power is sourced from (open-access renewables vs. DISCOM tariffs) can save 30–35% — a procurement decision, not a timing one, and not what this product does.

**Honest caveat:** cost and emissions savings don't always point the same direction — in coal/gas-heavy grids, optimizing for cost can occasionally increase emissions by up to 3%. India's midday solar glut is the exception that makes this easier here.

## 7. Business model

Aligned-incentive pricing: demand-response/flexibility vendors overwhelmingly charge a **commission on realized savings** (typically 10–30%) rather than a flat subscription. The broader flexibility-services market ($2.85B in 2024 → $8.44B by 2033) is built on exactly that model. It's the easiest "yes" from a first customer — we only make money if they save money.

## 8. Roadmap

1. **Fleet-level scheduler** — embed in one or two design-partner neoclouds' job queues, prove real ₹ savings, commission-based pilot.
2. **Aggregate flexibility data** — anonymized, aggregated "how much load shifted, when" becomes a genuine flexibility asset, instrumented from day one.
3. **Grid-facing resource** — pitch the aggregated flexibility to DISCOMs or DR aggregators as a dispatchable resource, reached bottom-up instead of via an early utility contract.

Phase 3 is a roadmap and a pitch, not a day-one feature.

## 9. What's not yet validated

- Data access is real but not clean — IEX and Grid-India/POSOCO data are publicly viewable, not exposed through documented APIs.
- No customer conversations yet — the 2–5% savings figure is from published research on other grids, not verified against a real neocloud's actual ToD bill.
- Herd-effect mitigation is designed, not tested at the scale where the problem would actually bite.

## Sources

Gartner (data center electricity demand) · Positive Current (grid-solutions startups) · Emerald AI · Data Center Dynamics (interconnection) · GridWise AI · Green Software Foundation (carbon-aware scheduling) · Saurenergy & Outlook Business (India duck curve) · Inc42 (India neoclouds) · ITK Research (data center opex) · MIT Sloan (flexible data centers) · GM Insights (demand response market) · PIB (India ToD tariff mandate) · Mercom India (open access renewables) · Codibly (demand response aggregator business models)

Full citations with links are in the published artifact: https://claude.ai/artifact/HKnepyEaASuRr2S5NsgJeG
