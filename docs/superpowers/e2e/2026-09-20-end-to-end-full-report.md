# End-to-end proof on a real Slurm (2026-09-20, full mode)

**Result: 33 of 33 checks passed** (after correcting one check, see Notes).

Real: the agent (unmodified), Slurm 26.05.2 in Docker (two nodes, four fake GPUs each, strict PrivateData, the agent as an Operator), the cloud's own code with a real database (the test database). Synthetic: the tariff (peak until a top-of-hour, then solar; boundary 19:00 IST), constant electricity prices, and the per-job start-time spread, which is switched off in the proof's cloud (it is unit-tested elsewhere). Not covered here: the dashboard, typed GPUs, real GPU hardware.

| Criterion | Check | Result | Detail |
|---|---|---|---|
| F | the lab has two idle nodes with 4 GPUs each | PASS | c1 idle gpu:4 | c2 idle gpu:4 |
| F | wattshift-agent check passes against the real Slurm as an Operator | PASS | mode: shadow (will never set future start times) |
| 1 | shadow: no start time was set on the job | PASS | eligible-submit = 0 s |
| 1 | shadow: the agent ran no write command (its own audit log) | PASS | 4 commands, 0 writes |
| 1 | shadow: the cloud recorded what it WOULD have done | PASS | planned 0.90 < baseline 1.32 Rs |
| 1 | shadow: the job started as soon as the GPUs freed up (not deferred) | PASS | 0 s after the blockers ended |
| 2 | autonomous: flex jobs are deferred into the cheap window | PASS | eligible [0, 0] s after the boundary |
| 4 | release-all sets every deferred job back to 'start now' (works without the cloud's help) | PASS | released 2 job(s); the cloud will stop planning for this site at the next sync |
| 4 | the cloud's kill switch turned on at the next sync | PASS |  |
| 4 | released jobs started as soon as the GPUs freed up | PASS | [-19, 0] s after the blockers ended |
| 4 | a released job is not managed again | PASS | released |
| 2 | autonomous: every flex job was deferred into the cheap window by the real cloud | PASS | {'A1': 0, 'A3': 0, 'A4': 0, 'A5': 0} |
| 2 | the start time in Slurm equals the one the real cloud decided | PASS | for all four jobs |
| 2 | GPU counts were read correctly from a real Slurm (--gres and --gpus styles) | PASS | {'A1': 2, 'A3': 1, 'A4': 1, 'A5': 2} |
| 2 | an array job and a dependent job are reported as skipped and never deferred | PASS | array, dependency |
| 2 | a non-flex job was never read or sent | PASS | job 89 |
| 5 | a job its owner changed is no longer managed | PASS | user_changed |
| 2 | a non-flex job started as soon as the GPUs freed up | PASS | 7 s after the blockers ended |
| 3 | the agent and the real cloud are both stopped | PASS |  |
| 5 | the owner's start time was respected (the agent did not fight it) | PASS | started 17 s after the owner's time; the time we had set was 393 s later |
| 3 | A1 started at the real cloud's decided time with the agent and the cloud stopped | PASS | +11 s |
| 3 | A4 started at the real cloud's decided time with the agent and the cloud stopped | PASS | +12 s |
| 3 | A5 started at the real cloud's decided time with the agent and the cloud stopped | PASS | +32 s |
| 2 | A1: the cloud's recorded start and end equal Slurm's accounting | PASS | start 1789911011, end 1789911032 |
| 2 | A1: the GPU count the cloud holds equals what was requested | PASS | 2 (requested 2) |
| 2 | A1: the measured saving matches a hand calculation | PASS | baseline 0.44, actual 0.30, saved 0.14 Rs (hand: 0.44, 0.30, 0.14; 1 min, 2 GPU) |
| 2 | A4: the cloud's recorded start and end equal Slurm's accounting | PASS | start 1789911012, end 1789911032 |
| 2 | A4: the GPU count the cloud holds equals what was requested | PASS | 1 (requested 1) |
| 2 | A4: the measured saving matches a hand calculation | PASS | baseline 0.22, actual 0.15, saved 0.07 Rs (hand: 0.22, 0.15, 0.07; 1 min, 1 GPU) |
| 2 | A5: the cloud's recorded start and end equal Slurm's accounting | PASS | start 1789911032, end 1789911053 |
| 2 | A5: the GPU count the cloud holds equals what was requested | PASS | 2 (requested 2) |
| 2 | A5: the measured saving matches a hand calculation | PASS | baseline 0.44, actual 0.30, saved 0.14 Rs (hand: 0.44, 0.30, 0.14; 1 min, 2 GPU) |
| 5 | A3 stayed abandoned after the restart | PASS | user_changed |

## Start accuracy with the agent and the cloud stopped

Seconds between the start time that was set and the real start (never negative: Slurm never started a job early):

- A1: +11 s
- A4: +12 s
- A5: +32 s

## Found by this proof and fixed

- **Proof's own check was wrong (harness, not the cloud).** The first full run reported 3 failures, "the plan was cheaper than the baseline" (planned 0.60 vs baseline 0.44 Rs for A1). By design the cloud replaces the time-limit estimate in `baseline_cost` with the job's measured runtime when it finishes (spec section 10), so the check compared a 2-minute estimate with a 1-minute measurement. The check now compares the measured baseline, actual and saved figures with a hand calculation (`check_measured` in `e2e/run.py`). The corrected check was applied to the rows this same run left in the test database (Slurm accounting and the cloud rows persist); the run was not repeated. Also confirmed on the same data: the two released jobs and the owner-changed job measured a saving of exactly Rs 0.00 (they started in the peak zone like the baseline), so the cloud does not invent savings.
- **Agent bug found by the fast run** is described in the fast report (pending array rows and `sacct`); it was fixed before this run.

Raw outputs: `docs/superpowers/e2e/raw/` (agent-check.txt, cloud-audit-events.txt, main-scontrol-A1.txt, release-all-cli.txt, shadow-scontrol-S1.txt)
