# Slurm spike 0 findings (2026-09-19)

Environment: Slurm **26.05.2**, image `giovtorres/slurm-docker-cluster:latest`
(`sha256:8b92cbd3bf7e51f910c7266c1dac13cf0c59059fa8db3c385bec76952a79b47b`), one controller, two dynamically
registered nodes with 4 fake GPUs each, backfill scheduler, accounting through slurmdbd, container clock in UTC.
Evidence files are in `agent/tests/fixtures/slurm/` (all real command output, none edited).

## Answers

| # | Question | Verdict | Evidence | What changes in the design |
|---|---|---|---|---|
| Q1 | Does a set `StartTime` hold a pending job and start it on time, even when resources are free? | **PASS** | `02-starttime-*.txt` | Mechanism confirmed. Add a start margin for scheduler lag; send times in UTC (see 1, 2). |
| Q2 | Is `squeue --start` usable as the baseline? | **Usable with guards** | `03-predicted-start-*.txt` | Guard against N/A and against a bogus +1 year value (see 4). |
| Q3 | Which `sacct` fields give real start, end, state? | **OK** | `04-accounting-completed.txt`, `04-accounting-terminal-states.txt` | Field list below; finished jobs must come from `sacct` (see 5). |
| Q4 | How are GPUs counted? | **Depends on cluster config** | `01-gres-and-users.txt`, `04-accounting-gres-enabled.txt` | Read GPUs from the live view when first seen (see 3). |
| Q5 | Per-job energy? | **Not testable in a container** | `04-accounting-energy.txt` | Deferred, as the spec already says. |
| Q6 | Unambiguous times, JSON? | **Use epoch; JSON works** | `05-machine-readable-times.txt` | Parsing method below. |
| Q7 | Does a decision survive a controller restart? | **PASS** | `06-restart-before.txt`, `06-restart-after.txt` | None: guarantee confirmed. |
| Q8 | User overrides, and can we mark our jobs? | **Marker is weak; use our own record** | `06-user-overrides.txt` | Concrete detection method (see 6). |
| Q9 | Privilege needed; arrays, dependencies, requeues | **Operator; broader than we want** | `06-privileges*.txt`, `06-special-jobs.txt` | Shadow-mode wording must change (see 7). |

## Measured start accuracy

Seconds between the start time we set and the real start (never before it in any run):

| Run | Setup | Gap |
|---|---|---|
| A1 | idle cluster, target set 45 s ahead | **+6 s** |
| A2 | GPUs free about 70 s before the target; job still held until the target | **+20 s** |
| D1 | controller restarted between setting and target | **+31 s** |
| A3 | `StartTime=now` (release) | running within **2 s** |

Slurm starts a deferred job at the next scheduling cycle after its start time. `SchedulerParameters` was unset here,
so the defaults apply (`sched_interval` 60 s, `bf_interval` 30 s), which bounds the lag at roughly a minute.

## What Slurm does, precisely (facts the agent relies on)

- **Setting a start time:** `scontrol update JobId=<id> StartTime=<time>` on a pending job. Accepted forms:
  `YYYY-MM-DDTHH:MM:SS`, `now+90` (seconds), `now+5minutes`, `now+1hour`, `HH:MM`. **`@epoch` is rejected.** A time in
  the past is accepted and means "eligible now" (`StartTime=Unknown`). Garbage is rejected with
  `Invalid time specification`.
- **Time zone:** an absolute time is parsed in the time zone of the **`scontrol` process** (a client set to
  `TZ=IST-5:30` sending `2026-09-20T12:00:00` stored epoch 1789885800, which is 06:30 UTC). The agent therefore runs
  `TZ=UTC scontrol update ... StartTime=<UTC>`.
- **Updating a start time also moves the job's `EligibleTime`** to that instant.
- **Reading times:** `SLURM_TIME_FORMAT=%s` gives epoch seconds in `sacct`, `squeue` and `scontrol`. Default output has
  no zone and shifts with the client's `TZ`. `squeue --json` and `sacct --json` also work (verbose; schema varies by
  Slurm version), so the agent uses pipe-delimited output (`-P`) plus epoch times.
- **Finished jobs vanish from `squeue`/`scontrol` after `MinJobAge` (300 s here)**, so the finished-job reporter must
  read `sacct`.
- **`sacct` fields to use:** `JobID, State, ExitCode, Submit, Eligible, Start, End, ElapsedRaw, TimelimitRaw, ReqTRES,
  AllocTRES, QOS, Partition, User, JobName`. **Avoid `SubmitLine`** (the whole submit command, may be private).
- **Terminal states seen:** `COMPLETED`, `FAILED` (with `ExitCode 3:0`), `TIMEOUT` (ran 85 s on a 60 s limit, so
  enforcement has slack), and `CANCELLED by <uid>` (match the prefix; a job cancelled while pending has `Start=None`).
- **GPUs, live:** `squeue -o %b` gives `gres/gpu:3`, and `scontrol show job` gives `TresPerNode=gres/gpu:3`.
- **GPUs, accounting:** absent from `ReqTRES`/`AllocTRES` by default (the accounting TRES list has no `gres/gpu`);
  present in both, and for pending jobs, once `AccountingStorageTRES=gres/gpu` is set. Typed GPUs
  (`gres/gpu:a100:4`) were **not tested** (this cluster has untyped GPUs).
- **Predicted start (`squeue --start`):** `N/A` for about the first 30 s after submit, then a stable, accurate value
  (11:59:00, the moment the blockers' 4-minute limit ended). When the *running* jobs have no time limit, Slurm
  predicts **exactly one year ahead** (2027-09-19 for a job submitted 2026-09-19) and never updates it.
- **User overrides:** an owner can change her own job's `StartTime`, hold it (`Reason=JobHeldUser`), release it,
  cancel it, or overwrite our `Comment` marker. Another user is refused (`Invalid user id for job`). `Reason` is
  unreliable: it reads `None` while a start time is holding a job, and Slurm itself sets requeued jobs to
  `Reason=BeginTime`.
- **`Comment` marker:** an Operator can set it on another user's job, but `sacct` did not show it, so it is a
  live-only marker.
- **Privileges:** an `Operator` account can set start times on others' jobs, list all pending jobs and read all
  accounting, **and can also cancel other users' jobs**. There is no defer-only permission. With
  `PrivateData=jobs,usage,users` a plain user cannot see other users' pending jobs; the Operator still can.
- **Special jobs:** an array shows as one `squeue` line (`41_[1-3]`, `%K` = `1-3`) and the Operator can set its start
  time in one call; a dependent job shows `Reason=Dependency` and `%E` = `afterok:42(unfulfilled)`; a requeued job shows
  `Restarts=1`.

## Design changes required

1. **Send UTC through a UTC client.** The agent runs `TZ=UTC scontrol update JobId=<ref> StartTime=<YYYY-MM-DDTHH:MM:SS>`
   and never epoch (rejected). Spec sections 6 and 8: add this.
2. **Plan with a start margin.** A deferred job starts at the target plus up to about a minute, never before. So
   `start_at <= latest_start - start_margin`, with a default margin of 120 s. Spec section 7: the hard limit becomes
   `start_at <= baseline_start + max_wait - start_margin`.
3. **Read GPUs from the live view when a job is first seen** (`squeue -o %b` / `TresPerNode`), and store them. `sacct`
   is only a cross-check, because its GPU columns depend on the cluster's configuration. Spec sections 6 and 10.
4. **Guard the baseline.** `predicted_start` is used only if it is present and `<= first_seen + max_wait`. It is
   re-read for the first few polls (it starts as `N/A`). Anything else, including a +1 year value, falls back to the
   first-seen time. Spec sections 7 and 10.
5. **Poll faster than `MinJobAge`, and report finished jobs from `sacct`.** The agent polls the live view at most every
   30 s and reads finished jobs from `sacct`, since Slurm forgets them after `MinJobAge` (300 s here). Map states by
   prefix (`CANCELLED by N`). Spec section 6.
6. **Detect user overrides from the agent's own record.** Keep `{ref: start_at applied}`; on each poll compare it with
   the job's `StartTime` (epoch) and state; a different value, a hold, or a missing job means stop managing it. The
   `Comment` marker is only a hint. Spec section 8 (replace "detects it" with this method).
7. **Reword "shadow mode is read-only".** Slurm cannot enforce it: on clusters with strict `PrivateData` the agent
   needs an Operator account, which can also modify and cancel. Shadow-mode read-only-ness is guaranteed by the agent
   code (an allow-list of exactly the commands it may run, and no write path in shadow mode), and the agent logs every
   Slurm command it runs so a customer can audit it. Spec sections 8 and 9.
8. **Arrays are easier than assumed** (the Operator can defer a whole array in one call), but v1 still leaves arrays,
   dependent jobs and requeued jobs alone, as the spec says; they are detected by `%K`, `%E` and `Restarts`. Spec
   section 8: no change, but note that arrays are a cheap later addition.

## Limits of this test

One controller and two nodes, fake GPUs, no energy plugin, no real workload, default scheduler settings, one Slurm
version (26.05.2; older versions differ, for example in `--json` support), untyped GPUs, no preemption, no partition
limits. A real cluster may behave differently under priority weights, preemption, partition rules or a tuned
`sched_interval`. The first real customer must repeat Q1 (start accuracy) and Q7 (restart survival) on their own cluster.

Harness detail worth keeping for the end-to-end plan: fake GPUs need a `gres.conf` with **four distinct existing device
files** (`Name=gpu File=/dev/null,/dev/zero,/dev/full,/dev/urandom`) **and** the workers started with
`--conf "Feature=cpu Gres=gpu:4"`; otherwise restarting the controller marks the nodes `INVALID_REG`.

## Recorded fixtures

- `00-cluster-info.txt`: version, scheduler settings, clock.
- `01-gres-and-users.txt`: a 2-GPU job with default accounting (GPUs absent from `sacct`).
- `02-starttime-pull-forward.txt`, `02-starttime-while-resources-busy.txt`, `02-starttime-release-now.txt`,
  `02-starttime-formats.txt`, `02-starttime-scheduler-intervals.txt`: start-time control and its formats and timing.
- `03-predicted-start-busy.txt`, `03-predicted-start-unlimited-running-jobs.txt`: predicted start, normal and with
  unlimited running jobs.
- `04-accounting-completed.txt`, `04-accounting-terminal-states.txt`, `04-accounting-gres-enabled.txt`,
  `04-accounting-energy.txt`: accounting fields, terminal states, GPUs with `gres/gpu` tracking, energy.
- `05-machine-readable-times.txt`: epoch time format, time-zone behaviour, JSON.
- `06-restart-before.txt`, `06-restart-after.txt`: start time surviving a controller restart.
- `06-user-overrides.txt`: owner changes, holds, cancels, the `Comment` marker.
- `06-privileges.txt`, `06-privileges-privatedata.txt`: what an Operator and a plain user can do and see.
- `06-special-jobs.txt`: arrays, dependencies, requeues.
