# Wattshift five-minute demo: storyboard and backup pack

## The story in one sentence

Wattshift watches the jobs waiting in a company's Slurm queue, moves the flexible ones to the cheap-electricity hours by
setting their start time, and proves the saving from what Slurm really did. If Wattshift itself goes down, Slurm keeps
the promise.

## What is real and what is a time-lapse (say this out loud, early)

- **Real:** Slurm (a real cluster in Docker, two nodes, eight GPUs), the Wattshift agent, the cloud's planning, the
  start times Slurm enforces, and the saving calculation from Slurm's own start and end times.
- **Time-lapse:** the electricity tariff clock. A real tariff changes on the hour, so a real demonstration would wait up
  to an hour. In the demo, every **two minutes** stand for one tariff hour.
- **Modeled:** the power draw of a job (GPUs times kW per GPU) and therefore the rupee figure. The demo jobs run for
  seconds, so the rupees are pennies. The percentage is what carries over to real jobs.

Suggested wording: "This is a time-lapse: two minutes here stand for one tariff hour. Everything else is real Slurm, the
real agent and the real cloud."

## The five minutes

| Time | Say | On the dashboard (Live demo) | In the terminal |
|---|---|---|---|
| 0:00 | "Electricity is expensive right now and turns cheap at HH:MM:SS. Three GPU jobs are waiting behind a busy cluster." | the jobs appear | `squeue`: three jobs pending |
| 0:25 | "Shadow mode. Wattshift only watches and plans. It must not touch Slurm." | jobs show "Would hold"; the timeline shows each job sliding from now into the cheap zone; a "planned" ₹ figure | `scontrol`: `Reason=Resources`, no start time set |
| 0:50 | "Now I switch the site to autonomous." (click **Autonomous**) | mode switch changes; jobs turn "Held" | `scontrol`: `Reason=BeginTime`, `EligibleTime` = the cheap moment |
| 1:20 | "The busy jobs are done and the GPUs are free. Slurm still holds our jobs, because Wattshift set their start time." | held jobs on the timeline | `sinfo`: both nodes idle; `squeue`: pending, `BeginTime` |
| 1:40 | "Now I stop the agent and the cloud completely." | "The cloud is not reachable. Jobs already held are still held: Slurm enforces their start times by itself." The last data stays on screen. | `squeue`: still held |
| 1:50 to ~4:00 | (countdown; talk about why this matters) | (banner stays) | "the jobs start in N s" |
| ~4:00 | "The jobs just started, with nothing of ours running." | (banner stays) | `squeue` and `sacct`: running, with the real start times |
| ~4:30 | "I bring the cloud back. It learns what really happened from Slurm's own records." | done jobs, run bars, **measured ₹ saved and the percentage** | the measured saving |

The whole run is 5 to 7 minutes from the first job to the measured result: the cheap moment is placed 230 to 350 seconds
after the start (the tariff clock ticks every two minutes, so where the clock falls decides), because the cloud will not
set a start time closer than 120 seconds ahead. If the presenter waits long before clicking **Autonomous**, the script
switches it after 45 seconds.

## How to run it

1. Docker Desktop running. The Slurm lab up once: `.\backend\.venv\Scripts\python e2e\lab.py up` (a few minutes; not part of the five minutes).
2. Optional, for the other dashboard tabs: start the normal API in `backend` (`uvicorn app.main:app --port 8000`). The demo's
   dashboard sends only the **Live demo** calls to the demo cloud and everything else to that API.
   `.\backend\.venv\Scripts\python e2e\demo.py`
3. Open `http://localhost:5174/#/live` and put it on screen. Press Enter in the terminal when ready.
4. When asked, click **Autonomous** on the dashboard. (If you do not, the script does it for you after 45 seconds.)
5. Options: `--auto` (no prompts), `--capture` (also save dashboard screenshots to `docs/demo/screens/`), `--dry-run` (print the storyboard), `--no-vite` (dashboard server already running).

## If something goes wrong (in this order)

1. **No Docker, or the lab will not start:** run the dashboard-only version. In `backend`:
   `python -m scripts.onboard_site --company Acme --site Pune-1 --gpus 64 --mode autonomous`, then
   `python -m scripts.simulate_agent --key <the key> --pace 4` against the normal API (about 25 seconds of scripted events:
   jobs appear, get held, one is changed by its owner, two finish and the saving is measured). Say clearly that this
   version uses scripted agent reports, not a live cluster.
2. **The live run fails halfway:** show the screenshots in `docs/demo/screens/` in numeric order (`01-shadow`,
   `02-held`, `04-measured`) and read the narration from the table above. There is no screenshot for the moment the
   cloud is stopped (it needs a page that was already open, so it cannot be captured after the fact): describe it from the
   table, and show the `squeue` output from the transcript.
3. **Nothing works:** read the transcript in `docs/demo/transcript.txt`, which is exactly what the terminal printed in a
   successful rehearsal (including the real `squeue`, `scontrol` and `sacct` output).

## Likely questions, honest answers

- **Is the saving measured?** The start and end times are real (from Slurm's accounting). The power draw is modeled
  (GPUs times kW per GPU), and the tariff in the demo is compressed. Against a real electricity bill it is still to be
  checked before any pay-on-savings agreement.
- **What if the cloud goes down?** Start times that are already set stay set and Slurm enforces them. `release-all`
  works without the cloud and sets every held job back to "start now".
- **What can it do inside a customer's Slurm?** It needs an Operator-level account. Slurm has no "defer only" permission,
  so the agent restricts itself to four kinds of command, logs every command, and starts in shadow mode so a customer can
  see what it would do first.
- **Does it touch every job?** No. Only jobs that match the customer's flex rules. A job the owner changes is left alone.
- **How big is the saving on real workloads?** On a real AI-training trace it is small (about 0.2% at default
  assumptions, up to about 2% if long jobs can wait) because a few very long jobs use most of the energy. It fits batch,
  inference and fine-tuning workloads best.
