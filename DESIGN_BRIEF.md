# Design Brief — Wattshift Dashboard

**Reference:** an "Electra" EV-fleet dashboard (dark violet, bento-grid, glowing card edges) that Akshay liked. This brief translates its structure and interaction patterns to Wattshift's actual data — not a copy of its content or literal palette.

## Layout & structure

Bento-grid of rounded cards (~16–20px radius) on a near-black deep-violet base. Cards use a slightly lighter surface tone with a soft ambient glow at the edges, not flat/flush panels.

**Top nav:** wordmark + icon (top-left) — pill-style tab switcher: *Dashboard / Jobs / Forecast / History* — search, notification bell, settings, avatar (top-right). Active tab shown as a filled rounded pill.

## Card-by-card mapping

| Reference card | Chart type | → Wattshift card | What it shows |
|---|---|---|---|
| Safety Score History | Flowing stream/area chart, floating % pills, dashed guide lines | **Price & Carbon Forecast** | 24h area chart blending ToD price and carbon intensity; pills mark "cheapest window" / "peak now"; dashed lines at current time and next price transition |
| Driving Stats | Hover-tooltip bar chart, 3 hatched/solid categories, weekly filter | **Job Queue by Tariff Window** | Daily bars split into solar / normal / peak segments; hatched = queued, solid = executed; same hover breakdown |
| Fuel Efficiency | Big number + bar row + 3 stats | **₹ Saved** | Total savings as the headline number; daily savings bar row; Today / Daily avg / Weekly total underneath — near 1:1 mapping |
| Electricity Used | Radial gauge, min/max ticks | **Current Grid Price** | Same gauge, but the arc itself is colored by price tier (solar/normal/peak); needle shows live ₹/kWh instead of MW |
| Tyre Pressure | Literal illustrated diagram (top-down car, 4 sensor readouts) | **ToD Clock** | Doesn't map to a car-free product — replaced with a literal 24-hour clock face showing colored arcs for solar/normal/peak hours and a "now" marker. Same instinct (a custom illustrated, domain-specific component), rebuilt for our domain instead of copied from theirs |

## Color system — meaning over decoration

Keep the dark-violet base (fits "grid at night" well). Do **not** keep the reference's pink/gold split as pure decoration — every accent color in Wattshift must carry real meaning:

- One consistent color = cheap/clean (solar hours) — used on the gauge arc, the forecast chart, job-window badges.
- One consistent color = expensive/peak — same, everywhere.
- No other accent colors compete with these two for attention; semantic color is the whole point here, not a style choice.

## Typography & numbers

Tabular figures (`font-variant-numeric: tabular-nums`) for every stat — ₹ amounts, MW, %, matching the reference's big-number treatment: label small and muted above or below, number large and bold.

## Build note

This is a direction, not final pixels — Akshay is applying additional design skills/passes on top of this brief. Treat it as the brief Claude Code should build a first pass from, expecting further refinement.
