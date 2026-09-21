---
name: Wattshift (Ground Hold)
description: Air-traffic control for computing. A job is a flight held at the gate until a cheap power slot opens.
colors:
  board: "#d6dce3"
  recess: "#c8d0d9"
  strip: "#f8f9fb"
  line: "#a3aeba"
  ink: "#10141a"
  ink-2: "#35404c"
  ink-3: "#4b5764"
  cheap: "#086b50"
  cheap-wash: "#b4dccb"
  peak: "#b3321c"
  peak-wash: "#efc8bf"
  night-board: "#14181d"
  night-recess: "#0d1014"
  night-strip: "#212932"
  night-line: "#3b4652"
  night-ink: "#eef1f5"
  night-ink-2: "#bcc5cf"
  night-ink-3: "#98a3af"
  night-cheap: "#44d2a4"
  night-cheap-wash: "#16503f"
  night-peak: "#ff7d5e"
  night-peak-wash: "#5b2618"
typography:
  display:
    fontFamily: "Archivo Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(2.3rem, 4.8vw, 3.8rem)"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Archivo Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(1.5rem, 3vw, 2.1rem)"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.012em"
  body:
    fontFamily: "Archivo Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 420
    lineHeight: 1.6
  strip-caption:
    fontFamily: "Archivo Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13.5px"
    fontWeight: 600
    lineHeight: 1.2
  figures:
    fontFamily: "Archivo Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 560
    lineHeight: 1.3
rounded:
  sm: "3px"
spacing:
  sm: "8px"
  md: "16px"
  lg: "32px"
  section: "48px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.board}"
    rounded: "{rounded.sm}"
    padding: "0 18px"
    height: "44px"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0 18px"
    height: "44px"
  strip:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "11px 16px 11px 38px"
  rack:
    backgroundColor: "{colors.recess}"
    rounded: "{rounded.sm}"
    padding: "14px 40px 14px 30px"
  field:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    height: "46px"
---

# Design System: Wattshift (Ground Hold)

## Overview

**Creative North Star: "The gate board."** A tower-desk board where every job is a paper strip and the only questions are *when does it leave* and *what does that slot cost*. The interface reads like a working desk: flat, matte, exact, a little physical. It refuses the dark-violet glowing-card dashboard the project started with.

Three commitments hold everywhere:

1. **One shared time ruler.** Every job, price band and log line reads against the same left-to-right time axis, so "now" is always in the same place.
2. **Two colours mean something.** Cheap green and expensive vermilion. Nothing else is a status colour. The middle band is deliberately neutral.
3. **State is shape.** A job's state is carried by the form of its strip, so it never depends on colour alone.

Day is the default (light concrete board). Night follows the system setting (charcoal board, same structure). The mode is decided by the visitor's system, not the page.

## Colors

- **Board** (`#d6dce3` day, `#14181d` night): the desk. Prose sits directly on it.
- **Recess** (`#c8d0d9` / `#0d1014`): the sunken rail and ruler ground. Holds racks and rulers.
- **Strip** (`#f8f9fb` / `#212932`): paper. Used only for job strips and form fields.
- **Line** (`#a3aeba` / `#3b4652`): hairlines, table rules, the flat rail.
- **Ink** (`#10141a` / `#eef1f5`): text, primary buttons, the "now" marker. Secondary text is Ink-2, tertiary Ink-3 (both checked at 4.5:1 or better on their surfaces).
- **Cheap** (`#086b50` / `#44d2a4`) and **Peak** (`#b3321c` / `#ff7d5e`): the two meanings. Each has a wash for band backgrounds. Text is never set in a wash colour.

Rules: never colour a control with Cheap or Peak except the emergency button (Peak outline). Interactive emphasis is Ink fill, never a third accent. Pure black and pure white are not used.

## Typography

One grotesque, used at three widths. There is no monospace: numbers are set in the same sans.

- **Archivo Variable** (width axis), self-hosted through `@fontsource-variable/archivo`. Three widths only: wide (112%) and heavy for headings, normal (100%) for body, slightly narrow (92%) for the small captions inside strips.
- **Numbers** use Archivo's tabular figures (`font-variant-numeric: tabular-nums`) wherever they line up (table columns, axis ticks, times in strips) and proportional figures for anything large and standalone. The first build used a monospace for numbers; the owner found it techy and busy, so it was removed.

Scale: display up to 3.8rem, section headlines 1.5 to 2.1rem, body 17px (16px on phones), strip captions 13.5px, chart labels 15 to 22px. Measure 60 to 62ch. Headings balance their wrap. Sentence case everywhere; no all-caps labels, no eyebrows above headings.

## Layout

A single 1240px column with 20 to 32px gutters. Pages are sequences of sections separated by a 1px ink rule, each with its own headline, not a grid of cards. The front-page hero is copy on the left (5 parts) and one big picture of a day of prices on the right (7 parts); on phones it stacks and the headline stays two lines. Screens with a secondary column (Live demo, Try it, What if) use 7/4 or 5/6 splits and collapse to one column below 1024px. Navigation is one line, 40px targets, six areas in three groups (see Speed convention). Every screen opens with its name and one plain sentence.

## Elevation & Depth

Physical, not glowing. Strips carry one offset-and-blur shadow (`0 1px 0` plus `0 3px 8px -3px`, tinted from the ink colour). Racks and rulers are recessed, flat. A held strip is pushed 22px out of its rack. No zero-offset halos, no glass, no blur as decoration.

## Shapes

One radius: 3px, for strips, racks, buttons, fields, dialogs. Bands and rulers are square. A finished strip has its top-right corner clipped (a 14px cut). Nothing is pill-shaped or circular except the two round marks on the timeline (a hollow circle where a job would have started, a solid one where it starts).

## Components

- **Strip.** One job. A small square zone flag at its top left (cheap green, expensive red, or neutral) and the zone written in words on the strip, so colour is never alone. Three parts: name and GPU count, state sentence with the times, cost. Forms: **flat** (nothing special), **dashed edge** (a plan only, nothing changed in Slurm), **cocked** (held: pushed out of the rack), **notched** (finished), **struck** (released: text struck through), **doubled edge** (changed by its owner).
- **Rack.** A recessed panel with a flat rail on the left; strips hang from it. Held jobs are grouped in their own rack.
- **Day picture.** The signature graphic: a day of electricity prices as a skyline of three blocks (night grey, daytime green, evening red), block height proportional to price, each block labelled directly with its price and its hours in plain words. Jobs are small paper tokens with a zone flag that sit on the block they start in. On the front page three tokens slide from the evening block to the daytime block, with a dashed outline left where they would have started and an arrow labelled "Wattshift holds them". Used again for the drag-a-start-time demo and for Prices today, where a line marks now and queued jobs appear as labelled dots.
- **Forecast chart.** One step line over the same coloured bands, the cheapest hour boxed, values readable by hover or the arrow keys, and a plain sentence under it. No second axis, no legend to decode.
- **Time ruler.** Zone bands behind rows; a hollow circle where a job would have started, a hatched bar while it is held, a solid dot at its start time, a solid bar for the real run, a 2px "now" line with a small ink label under the axis.
- **Buttons.** Rectangular, 44px tall (36px small). Primary is Ink fill; secondary is a 1px Ink outline; quiet is a Line outline. The emergency button is a Peak outline that fills when armed (two clicks).
- **Segmented switch.** Two labelled sides, the pressed side filled Ink.
- **Notice.** An inverted bar (Ink background, Board text) for anything that must be seen. Never colour-coded.
- **Term.** A dotted-underlined word that opens the "Words used here" panel at its entry. The panel is non-modal, so it does not block the page.
- **Measured comparison.** Two horizontal bars on one zero baseline, the same job started at once and at the hour Wattshift chose, each labelled directly with its watts and its cost per GPU-hour, with a table twin. The bars are neutral (not zone coloured) because each is an average over several runs. The finding is the headline sentence, and it says plainly when nothing was saved.
- **Tables.** Every chart and rack has a table version, switched with a plain button. Numeric columns are right-aligned, tabular figures.
- **Speed note.** A boxed sentence under a screen's title that says which speed the screen runs at. Dashed border: time-lapse. Solid border: real speed. The same word choice everywhere ("Time-lapse", "Real speed"), each linked to its entry in the words panel.
- **Fields.** Strip-coloured, 1px Ink-3 border, label above, hint below, never placeholder-as-label.

## Speed convention

The project shows two things that must never be mistaken for each other. The **time-lapse demo** runs the tariff clock 60 times faster (2 minutes stand for 1 hour) so a wait of hours fits in minutes. Everything else runs at **real speed**. The distinction is carried by four repeated devices:

- **Header groups.** "Time-lapse demo" (Live demo) sits behind a dashed divider; "Real speed" (Prices today, What if, Measured, Try it) behind a solid one. Start here stands alone.
- **Speed note.** Every screen states its speed under its title (dashed box for time-lapse, solid for real).
- **Line style.** Dashed means time-lapse (the note border, the timeline caption); solid means real. Do not use a dashed border for anything else at page level.
- **Real-speed translation.** In a time-lapse view every wait is followed by what it would be at true speed, in the same row ("waits 3 min 41 s" then "1.8 h at real speed"), and the timeline sentence says the same in words. The recorded-run fallback counts as time-lapse.

The front page has a section, "Two ways to see it", that offers both paths and one factual block on the run at true speed (start times, what was stopped, checks passed). Do not call the demo "live at real speed" anywhere.

## Do's and Don'ts

Do: say what a thing is in plain words before naming it. Give every chart a headline that states its finding in a sentence, and label the marks directly. Put the recorded run next to the claim it proves. Say when a figure is a best case. Label every figure measured, modelled, or demo. Keep one time axis per screen. Use shape for state and colour only for cheap and expensive. Respect reduced motion (the replay stops and shows its final frame).

Don't: reintroduce dark-violet gradients, glow shadows, pill tabs, rings or gauges as decoration. Don't put a coloured side stripe on anything. Don't set numbers in a monospace face. Don't use an eyebrow, a section number that isn't a real sequence, or gradient text. Don't use an em-dash anywhere a visitor reads (a test enforces this). Don't add a third status colour. Don't show a rupee figure without saying it is modelled.
