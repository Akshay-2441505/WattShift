# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Anyone who opens the link: the project is being displayed as a showcase, not pitched, so no prior knowledge of energy tariffs, GPUs or job schedulers can be assumed. They arrive cold, often without a guide, and need to work out what this is and whether it works. (Confirmed by the owner: "can be anyone", no pitch yet.)

Later audience, per `BUSINESS_CASE.md` and not the current focus: mid-market GPU cloud operators in India, whose single largest cost is electricity.

## Product Purpose

Wattshift cuts the electricity bill of a GPU cluster by starting flexible jobs in the cheap hours. India already charges different prices at different times of day (time-of-day tariffs, plus the IEX day-ahead market), but almost nobody times their jobs around them. Wattshift moves the jobs that can wait to the cheap hours and measures the saving.

It has two working parts:
- **Original version:** submit a job with a length and a deadline; Wattshift picks the cheapest window that still meets the deadline and runs it (on Kaggle GPUs in the demo).
- **Automatic version:** a small agent sits next to a company's Slurm queue, sets start times on the jobs the company allowed, and the cloud measures the saving from Slurm's own start and end times.

Success for this surface: a stranger understands the idea in about a minute, watches it work, and can tell what is real and what is modelled.

## Positioning

It delays jobs by setting a start time inside Slurm itself, so Slurm keeps the promise even if Wattshift is switched off. It starts in a watch-only "shadow mode", has an emergency release, and measures the saving from Slurm's real start and end times. Other products in this space pause, shift or relocate whole workloads for the grid's benefit; this one times a customer's own jobs against their own electricity tariff.

## Operating Context

- The customer's queue is Slurm. The agent talks to it with four kinds of command only and logs every one.
- Trust ladder: shadow mode (read only, shows what it would do), then autonomous on a small group of flexible jobs, with a release-all button.
- The five-minute demo is a time-lapse: two minutes stand for one tariff hour. Slurm, the agent and the cloud are real; the tariff clock is compressed; power draw and rupees are modelled.
- Money is in Indian rupees. Tariff: MERC order for HT industrial, FY 2026-27 (Maharashtra, MSEDCL). Prices: IEX day-ahead.

## Capabilities and Constraints

- The saving is a modelled figure (GPUs x kW per GPU x tariff), not a measured electricity bill. Only the start and end times come from Slurm. It has never been compared with a real bill.
- Only the Maharashtra HT-industrial tariff is verified.
- On a real AI-training trace the saving is small (about 0.2%, up to about 2%), because a few very long jobs use most of the energy. The percentage in the demo (about 32%) comes from moving peak-hour jobs to solar hours and does not carry to every workload.
- The demo runs on a Docker Slurm with fake GPUs. It has not been run on a real customer cluster.
- The agent skips array jobs, jobs that depend on other jobs, and re-queued jobs.
- Not deployed, no login. Write actions need an API key; reads are open.
- The project name "Wattshift" is a placeholder.
- Terms already in use: shadow mode, autonomous, held, would hold, release all, planned versus measured saving, cheap / normal / peak hours.

## Brand Commitments

Name: Wattshift. Interface copy is sentence case, plain words, rupees with the rupee sign. Colour must mean something: one colour for cheap hours, one for expensive hours, used the same way everywhere. Every figure has to say whether it is measured, modelled, or from the demo. (The earlier dark violet, glowing-card look came from a reference dashboard and is not a brand commitment.)

## Evidence on Hand

- Recorded end-to-end proof on a real Slurm in Docker: fast run 28 of 28 checks, full run 33 of 33 (`docs/superpowers/e2e/`).
- The five-minute demo, with a transcript and screenshots in `docs/demo/`. It measured Rs 0.28 saved, 31.99% lower, on 3 jobs; Slurm started held jobs 4 to 44 seconds after their set time, never early.
- A backtest on a real public GPU-cluster job log (Helios, SenseTime, CC-BY-4.0): about 0.2% at default assumptions.
- Real IEX day-ahead prices and the verified MERC tariff.
- Absent, so never to be invented: customers, testimonials, pricing, benchmarks on real customer clusters, a saving confirmed against a real bill, and any production deployment.

## Product Principles

1. Plain words first. Never lead with a term the visitor has not been given.
2. Show it working before explaining it.
3. Every number says where it comes from: measured, modelled, or demo.
4. The safety story is part of the product: watch first, delay by setting a start time, and Slurm keeps the promise if Wattshift stops.
5. Colour carries meaning; decoration does not.

## Accessibility & Inclusion

Keep the existing commitments: every chart has a table alternative, states are shown with words and shapes as well as colour, text passes WCAG AA contrast, and layouts work on a phone. Plain language is itself an inclusion requirement here: the reader may have no energy or computing background.
