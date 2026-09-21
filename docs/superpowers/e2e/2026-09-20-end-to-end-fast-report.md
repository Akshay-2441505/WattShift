# End-to-end proof on a real Slurm (2026-09-20, fast mode)

**Result: 28 of 28 checks passed.**

Real: the agent (unmodified), Slurm 26.05.2 in Docker (two nodes, four fake GPUs each, strict PrivateData, the agent as an Operator), the cloud's own code with a real database (the test database). Synthetic: the tariff (peak until a top-of-hour, then solar; boundary 19:00 IST), constant electricity prices, and the per-job start-time spread, which is switched off in the proof's cloud (it is unit-tested elsewhere). Checks named 'stand-in cloud' used a small stand-in for the cloud because the real cloud cannot hand out a start time a few minutes away; every other check used the real cloud. Not covered here: the dashboard, typed GPUs, real GPU hardware.

| Criterion | Check | Result | Detail |
|---|---|---|---|
| F | the lab has two idle nodes with 4 GPUs each | PASS | c1 idle gpu:4 | c2 idle gpu:4 |
| F | wattshift-agent check passes against the real Slurm as an Operator | PASS | mode: shadow (will never set future start times) |
| 1 | shadow: no start time was set on the job | PASS | eligible-submit = 0 s |
| 1 | shadow: the agent ran no write command (its own audit log) | PASS | 4 commands, 0 writes |
| 1 | shadow: the cloud recorded what it WOULD have done | PASS | planned 0.90 < baseline 1.32 Rs |
| 1 | shadow: the job started as soon as the GPUs freed up (not deferred) | PASS | -1 s after the blockers ended |
| 2 | autonomous: flex jobs are deferred into the cheap window | PASS | eligible [0, 0] s after the boundary |
| 4 | release-all sets every deferred job back to 'start now' (works without the cloud's help) | PASS | released 2 job(s); the cloud will stop planning for this site at the next sync |
| 4 | the cloud's kill switch turned on at the next sync | PASS |  |
| 4 | released jobs started as soon as the GPUs freed up | PASS | [-18, 1] s after the blockers ended |
| 4 | a released job is not managed again | PASS | released |
| 2 | autonomous: every flex job was deferred into the cheap window by the real cloud | PASS | {'A1': 0, 'A3': 0, 'A4': 0, 'A5': 0} |
| 2 | the start time in Slurm equals the one the real cloud decided | PASS | for all four jobs |
| 2 | GPU counts were read correctly from a real Slurm (--gres and --gpus styles) | PASS | {'A1': 2, 'A3': 1, 'A4': 1, 'A5': 2} |
| 2 | an array job and a dependent job are reported as skipped and never deferred | PASS | array, dependency |
| 2 | a non-flex job was never read or sent | PASS | job 54 |
| 5 | a job its owner changed is no longer managed | PASS | user_changed |
| 2 | a non-flex job started as soon as the GPUs freed up | PASS | 9 s after the blockers ended |
| 3 | (stand-in cloud) the start time in Slurm equals the one handed out | PASS | {'F1': 148, 'F2': 148} |
| 3 | (stand-in cloud) Slurm held both jobs although the GPUs were free | PASS | the blockers ended 75 s before the set time |
| 3 | (stand-in cloud) the agent and the stand-in cloud are both stopped | PASS |  |
| 3 | (stand-in cloud) F1 started at its set time with the agent and the cloud stopped | PASS | +15 s |
| 3 | (stand-in cloud) F2 started at its set time with the agent and the cloud stopped | PASS | +15 s |
| 5 | the owner's start time was respected (the agent did not fight it) | PASS | started 6 s after the owner's time; the time we had set was 1972 s later |
| 2 | F1: the cloud's recorded start and end equal Slurm's accounting | PASS | start 1789909089, end 1789909109 |
| 2 | F1: the GPU count the cloud holds equals what was requested | PASS | 1 (requested 1) |
| 2 | F2: the cloud's recorded start and end equal Slurm's accounting | PASS | start 1789909089, end 1789909109 |
| 2 | F2: the GPU count the cloud holds equals what was requested | PASS | 2 (requested 2) |

## Start accuracy with the agent and the cloud stopped

Seconds between the start time that was set and the real start (never negative: Slurm never started a job early):

- F1: +15 s
- F2: +15 s

## Notes

- Fast mode: A1, A4 and A5 (deferred by the REAL cloud to the cheap window) were cancelled while held; only full mode waits for them to start.

## Found by this proof and fixed

- **Agent bug (the first fast run failed on it).** When a pending array job (`35_[1-2]`) starts its first task, Slurm replaces that queue row with the tasks. The agent then asked `sacct` about the vanished id, and a real `sacct` refuses the whole call ("Bad job/step specified ... JobID includes unexpected non-numeric characters"). Every later cycle failed the same way, so while any array job was known the agent stopped reporting to the cloud (fail-open: nothing was deferred or broken, but nothing was learned either, and the owner-override check timed out). Fix: `agent/wattshift_agent/cycle.py` only asks `sacct` about ids of the form `123` or `123_4`; a vanished array row is closed as `OTHER` (v1 never manages arrays, so there is no result to measure). Test-first: `test_a_pending_array_row_that_leaves_the_queue_never_breaks_the_cycle`, with the fake `sacct` now refusing such ids like the real one. Agent suite 183 passed.
- **Recorded real outputs** (`agent/tests/fixtures/real/`, 7 parser tests): the `squeue -o` field set with epoch times, `sacct -S now-14days`, and `--gpus=N` as `TresPerJob=gres/gpu:N` all match what the parsers assumed. A `|` inside a job name survives (the name is the last field). An unknown job id gives exit 1 and `Invalid job id specified`.

Raw outputs: `docs/superpowers/e2e/raw/` (agent-check.txt, cloud-audit-events.txt, main-scontrol-A1.txt, release-all-cli.txt, shadow-scontrol-S1.txt)
