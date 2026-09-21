# Wattshift automation: a Slurm agent that defers flexible jobs to cheap hours

Status: approved on 2026-09-19. **Spike 0 (a real Slurm) was run the same day** and its findings are already folded
into sections 6-10 and 16; the evidence is in `docs/superpowers/spikes/2026-09-19-slurm-spike-findings.md`. No product
code yet.

## 1. Why this exists

The v1 demo takes jobs from a form and runs them itself on Kaggle. That cannot work in the real world. A real
customer already has a scheduler and users who submit jobs to it; Wattshift has to sit **inside that system and act
with no human in the loop**: see each job, decide when it should start, make that happen, and report what it saved.

Wattshift stops being the thing that *runs* jobs. It becomes an automatic **control layer** that changes *when* a
customer's own scheduler starts flexible jobs.

## 2. Decisions made (from the brainstorm)

| Question | Decision |
|---|---|
| Where does "this job can wait" come from, with no human at Wattshift? | **A flex queue plus operator-set rules.** Only jobs in the flex queue, or matching a rule, are ever touched. |
| First scheduler to support | **Slurm.** (Yotta publicly says it uses Slurm for batch AI jobs; research HPC centres use it.) |
| Where does the connector run? | **A small agent inside the customer's network**, outbound connections only. The brain, prices and tariffs stay in Wattshift's cloud. |
| How does the agent control Slurm? | **A sidecar that sets each job's start time** with Slurm's own start-time control. Not a submit plugin (can block all submissions if it fails) and not a wrapper command (changes every user's workflow). |

## 3. Goals and non-goals

Goals (first version):
1. Flex jobs get a start time in the cheapest window before their limit, with no human action.
2. No job is lost, and none starts later than its limit, **even if the agent or the cloud is down**.
3. A company can start in **shadow mode** (read-only) and see what it would have saved before granting any control.
4. Savings are recorded per job and shown on the dashboard, labelled *modeled*.

Non-goals (first version): Kubernetes or other schedulers; an "assisted" approve-each-plan mode; learning which jobs
are flexible from history; measured (not modeled) power; reconciliation with the real electricity bill; tariffs for
utilities other than MSEDCL; a fully on-premises install; invoicing; optimising across several sites at once.

## 4. Architecture

```
  Company network                              Wattshift cloud
  +-----------------------------+              +-----------------------------+
  |  Slurm cluster              |              |  Prices and tariff data     |
  |    ^ start times   | jobs   |              |            |                |
  |    |               v        |   job facts  |            v                |
  |  Wattshift agent  ----------+------------->|  Planner                    |
  |  (rules run here) <---------+--------------|            |                |
  +-----------------------------+ start times  |            v                |
       agent only calls out                    |  Savings and audit log      |
                                               +-----------------------------+
```

(The same picture, drawn properly, is in the chat where this design was agreed.)

One job's life:

```
user submits to the flex queue -> agent sees it (within one poll, ~30 s) -> cloud picks the cheapest window before
the limit -> agent sets the start time in Slurm -> Slurm starts the job by itself -> agent reports the real start and
end -> cloud records the saving
```

The cloud never logs in to the customer. The agent evaluates the company's rules **locally**, so job names, users and
scripts never leave the customer's network.

## 5. Flexibility rules (agent side)

A file the operator edits once, e.g. `flex-rules.yaml`:

```yaml
rules:
  - match: { qos: flex }                       # anything submitted to the flex QoS
    max_wait: 24h
  - match: { partition: batch-infer, name: "^eval-.*" }
    max_wait: 12h
default: none                                  # anything else is never touched
```

First match wins. A job matching no rule is invisible to Wattshift. `max_wait` means: the job may start up to this much
later than it otherwise would have.

The rules live in the agent's single config file (`agent.yaml`, see `agent/agent.example.yaml`) next to the cloud URL,
the mode and how to reach Slurm. `name` is a regular expression; `qos`, `partition`, `user` and `account` are exact.
`max_wait: 0` explicitly excludes what it matches. The site key never goes in the file (`key_file` or the
`WATTSHIFT_SITE_KEY` environment variable).

## 6. Agent to cloud contract

`POST /agent/v1/sync`, authenticated with a per-site key, called every ~30 s. Safe to repeat: everything is keyed by
`ref` (the Slurm job id), so a retried request changes nothing.

Request:

```json
{
  "agent_version": "0.1.0",
  "mode": "shadow | autonomous",
  "sent_at": "2026-09-19T12:00:00Z",
  "jobs": [
    {"ref": "48211", "state": "PENDING", "submit_time": "...", "gpus": 8, "time_limit_min": 90,
     "max_wait_min": 1440, "predicted_start": "..."},
    {"ref": "48190", "state": "COMPLETED", "start_time": "...", "end_time": "...", "gpus": 8}
  ],
  "applied": [{"ref": "48211", "start_at": "...", "ok": true, "error": null}]
}
```

Response:

```json
{"decisions": [{"ref": "48211", "start_at": "2026-09-20T06:45:00Z"}],
 "release_all": false, "next_poll_s": 30}
```

In `shadow` mode the cloud still plans and logs the decisions ("would have"), and the agent never applies them.
All times are UTC.

**Extra fields (added while building the cloud core).** Each job may also carry `"override": false` and
`"skipped_reason": null`; the request may also carry `"released": []` and `"release_all": false`.
- `override` (bool): the agent saw a change it did not make, so the cloud stops managing the job.
- `skipped_reason` (`array | dependency | requeue`): the job is never planned.
- `released` (refs): jobs the agent set to start now after `release_all`.
- request `release_all` (bool): the operator ran `release-all` on the agent, so the cloud turns the site's kill switch
  on. The response `release_all` stays true until an operator turns it off, and while it is true the cloud plans
  nothing and sends no decisions.
- `state` is one of `PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT, OTHER` (the agent maps Slurm's longer
  list by prefix; everything else is `OTHER`).
- The **effective mode** is autonomous only if the site setting and the agent's `mode` both say so. Otherwise the
  cloud records decisions but returns none, and it logs `mode_mismatch`. If a shadow-mode agent reports an `applied`
  start time, the cloud logs `shadow_violation`.

Agent rules, verified against a real Slurm (Spike 0):
- **Applying a start time:** `TZ=UTC scontrol update JobId=<ref> StartTime=<YYYY-MM-DDTHH:MM:SS>`. Slurm parses an
  absolute time in the time zone of the `scontrol` process, and rejects epoch (`@1789...`), so the agent always runs
  it with `TZ=UTC`. `StartTime=now` releases a job.
- **Reading:** pipe-delimited output (`-P`) with `SLURM_TIME_FORMAT=%s` (epoch seconds) from `squeue`, `scontrol`
  and `sacct`. Default time output has no zone and shifts with the client's `TZ`.
- **`gpus`** is read from the live view the first time a job is seen (`squeue -o %b`, e.g. `gres/gpu:3`) and stored.
  `sacct` only shows GPUs when the cluster sets `AccountingStorageTRES=gres/gpu`, so it is a cross-check, not the source.
- **`predicted_start`** (`squeue --start`) is sent only when present **and** no later than `first_seen + max_wait`.
  It is `N/A` for about the first 30 s, and Slurm reports exactly one year ahead when the running jobs have no time
  limit; both cases are omitted and the baseline falls back to the first-seen time.
- **Finished jobs** come from `sacct` (Slurm forgets them after `MinJobAge`, 300 s by default). State strings are matched
  by prefix (`CANCELLED by 2001`). The agent polls the live view at least every 30 s.
- **Fields read from `sacct`:** `JobID, State, ExitCode, Submit, Eligible, Start, End, ElapsedRaw, TimelimitRaw, ReqTRES,
  AllocTRES, QOS, Partition, User, JobName`. Never `SubmitLine` (the whole submit command, possibly private).

## 7. Planning rules (cloud)

- **Hard limit.** `latest_start = baseline_start + max_wait`, where `baseline_start` is Slurm's own predicted start
  when first seen and plausible (falling back to the first-seen time; see section 6). A decision is never later than
  `latest_start - start_margin`; equivalently the job finishes by `latest_start + time_limit`.
- **Start margin.** Slurm starts a deferred job at its start time plus up to about one scheduling cycle, and never
  before it. Measured lag was 6 s, 20 s and 31 s (the last after a controller restart); the default cycle bounds it at
  roughly a minute. The margin is a per-site setting, **default 120 s**.
- **Uses the declared time limit** as the job's length (a safe over-estimate). Real runtime is used only when
  measuring savings.
- **Defer only for a strictly lower bill.** A job whose planned start does not bill strictly less than its baseline
  start is left to Slurm (no decision). This also means the autonomous planner never produces a negative saving from
  an IEX dip inside a peak block.
- **Capacity.** Shifted GPU-minutes per 15-minute block are capped at `shift_capacity_share x GPUs x 15`, and at
  `power_limit_kw / kw_per_gpu x 15` when the site has a power limit, so the plan cannot create a new maximum-demand
  peak (which raises the demand charge and can erase the saving). Reuses the existing allocator with the `weight`
  (GPU count) already added for the backtest. Only work Wattshift shifted uses this capacity.
- **Prices.** `IEX price x ToD multiplier` for ranking; the ToD tariff for the bill, exactly as today.
- Jobs whose window cannot be found run as normal (no decision is sent).

## 8. Safety and failure behaviour

| Situation | Behaviour |
|---|---|
| We never *hold* a job | We only set a future start time, and Slurm enforces it. |
| Agent or cloud dies after a decision | The job still starts at its set time. |
| No decision for a job | It runs exactly as it would have without Wattshift. |
| The agent cannot apply a change | It leaves the job alone and reports the error. |
| A user changes or cancels a flex job | Wattshift stops managing that job and never fights the user. Detection is from the agent's **own record**: it stores `{ref: start_at it applied}` and on every poll compares it with the job's `StartTime` (epoch) and state. A different start time, a hold (`Reason=JobHeldUser`) or a missing job means "stop managing". Slurm's `Reason` field is not used (it reads `None` while a start time is holding a job, and Slurm itself puts requeued jobs at `BeginTime`), and a `Comment=wattshift:managed` tag is only a live hint (an owner can overwrite it and `sacct` does not show it). |
| Decision arrives after the job started | Ignored. |
| `release_all` (from the agent command or dashboard) | The agent sets every job it deferred to start now. |
| Any decision or action | Written to the audit log (cloud) and a local log (agent). |
| Job arrays, dependencies, requeues, preemption | Out of scope for v1: those jobs are left alone and counted as "skipped". They are recognised by an array id (`%K` non-empty, id like `41_[1-3]`), a dependency (`%E` non-empty, `Reason=Dependency`), and `Restarts > 0`. Slurm can defer a whole array with one call, so arrays are a cheap later addition. |
| Commands the agent runs | Only: `squeue`, `sacct`, `scontrol show job <id>`, and `scontrol update JobId=<id> StartTime=<value>`, where `now` (a release) is always allowed, so a shadow agent can undo its own earlier deferrals, and an absolute UTC time (a deferral) only in autonomous mode. It never calls `scancel` or anything else that changes or ends a job. The `Comment=wattshift:managed` marker was dropped: it is weak (Spike 0) and overwriting a user's comment is a change we do not need to make. Every command is written to the local audit log. |

## 9. Trust modes

Per site, chosen by the company:

1. **Shadow.** The agent only *reads*: in this mode it has no code path that writes to Slurm. Wattshift shows what it
   *would* have saved. This is the live version of the Backtest page and the low-risk way to start.
   **Caveat found in Spike 0:** Slurm cannot enforce this. With the common strict setting `PrivateData=jobs,...` a plain
   user cannot see other users' pending jobs, so even shadow mode needs an **Operator-level** account, and an Operator
   can also modify and cancel jobs. The guarantee therefore rests on the agent's allow-list and audit log (section 8),
   which the customer can inspect; the agent is open to review for that reason.
2. **Autonomous.** The agent applies start times using an Operator-level Slurm account. Slurm has no "defer-only"
   permission, so the same allow-list and audit log are the safeguard, and jobs are limited to the flex rules.
3. **Assisted** (a person approves plans) is designed for but **not built** in v1.

## 10. Measuring savings

- **Baseline:** cost if the job had started at `baseline_start` (Slurm's own prediction at first sight when it is
  present and plausible, otherwise the first-seen time). This is fairer than "the moment it was submitted", because a
  busy cluster would have made the job wait anyway. Spike 0 showed the prediction is accurate when running jobs have
  time limits, and a meaningless +1 year when they do not, hence the plausibility check in section 6.
- **Actual:** cost at the real start and elapsed time reported by Slurm accounting (`Start`, `ElapsedRaw`).
- **Power:** the stored GPU count (from the live view, section 6) x a per-site kW-per-GPU setting in v1 (modeled).
  Later: Slurm's per-job energy record (`ConsumedEnergyRaw`, empty unless the cluster runs an energy plugin, so not
  testable in Spike 0) or GPU monitoring.
- **Cost:** the existing `bill_cost` under the site's tariff. Savings are labelled *modeled* until checked against the
  company's bill (needed before any pay-on-savings deal).

## 11. Data sources

| Data | Source |
|---|---|
| Pending and finished jobs | Slurm, via the agent |
| Prices | IEX (built) |
| Tariff | A catalogue with one verified entry per utility and category, versioned, each with an expiry (MSEDCL HT-I(A) FY 2026-27 exists) |
| Site facts (utility, tariff, GPUs, power limit, kW per GPU) | Entered once at onboarding |
| Measured power, meter or bill data | Later |

## 12. Data model (new; existing tables untouched)

- `companies`, `sites` (utility, tariff category, gpus, power_limit_kw, kw_per_gpu, mode), `site_keys` (hashed).
- `tariff_catalogue` (utility, category, valid_from, valid_until, rules, base_rate, verified, source).
- `managed_jobs` (site_id, ref, state, submit_time, gpus, time_limit_min, max_wait_min, baseline_start,
  planned_start, applied_start, actual_start, actual_end, baseline_cost, actual_cost, saved) with a unique
  `(site_id, ref)`.
- `managed_jobs.plan_status` is one of `pending, planned, unplaceable, skipped, abandoned, released`; `sites` also
  carries `shift_capacity_share`, `start_margin_s` (default 120), `release_all` (the kill switch), `last_seen_at`,
  `last_agent_mode` and `agent_version`.
- `decisions` and `audit_log` (append only by convention; a DB-level guarantee is still to do).
- The existing `jobs` table, dispatcher and Kaggle runner remain for the demo only.

## 13. Code layout

- **New:** `agent/` (its own package, tests and config): `config.py` and `rules.py` (the YAML and the flex rules),
  `parse.py` (Slurm text to facts), `runner.py` (the allow-list, the fixed environment, one audit line per command),
  `slurm.py` (the five things it asks Slurm), `state.py` (local SQLite record and outboxes), `cycle.py` (discover,
  refresh, build the request, `run_once`), `applier.py` (apply a decision, `release_all`), `client.py` (the one HTTPS
  call), `cli.py` (`run`, `release-all`, `status`, `check`).
- **New (cloud):** sync endpoints, site and tenant tables, decision and audit tables, a sync service that reuses the
  allocator, tariff and savings code.
- **Changed (done 2026-09-21):** the dashboard gains a **Live cluster** tab: a site picker, per-job states, the mode
  switch, the kill switch, the agent's connection status, the measured saving kept separate from the plan, a "shift
  timeline" (where each job would have started and where it starts instead, over the tariff zones) and an activity feed.
  It is served by one read endpoint per site (`GET /sites`, `GET /sites/{id}/view`); the cloud fills `actual_cost` and
  `saved` itself when the agent reports a finished job (`backend/app/measure.py`). The manual Submit job form is kept
  only in demo mode.
- **Kept as is:** the allocator, tariff and savings maths, IEX ingestion, forecast, backtest.

## 14. Testing

1. Unit tests on recorded `squeue`, `scontrol` and `sacct` output (rules, parsing, the hard-limit guarantee).
2. Agent tests that run its real subprocess calls against **fake Slurm executables** on the path.
3. Contract tests for the sync API (idempotency, auth, shadow versus autonomous).
4. **A real Slurm in Docker** (Docker is installed here; pulling an image needs the user's permission). Cases:
   shadow changes nothing; autonomous sets start times inside the cheapest window and Slurm starts the jobs; kill the
   agent and the cloud and the jobs still start on time; `release_all` clears start times; a user's own change is
   respected; the savings row matches a hand calculation.

## 15. Suggested build order (this is too large for one plan)

Each step ends with something that works and is tested on its own, and gets its own implementation plan:

1. **Spike 0: DONE 2026-09-19 (see section 16).** Ran a real Slurm in Docker. Output was answers plus recorded fixtures,
   and it changed the design in the places marked "Spike 0" above.
2. **Cloud core: DONE 2026-09-20** (plan: `docs/superpowers/plans/2026-09-20-cloud-core.md`). Companies, sites, tariff
   catalogue, the sync endpoint and planner, decision and audit log. Tested with no agent, by posting recorded job facts.
3. **Agent: DONE 2026-09-20** (plan: `docs/superpowers/plans/2026-09-20-slurm-agent.md`): Slurm reader, rules, sync
   loop, applier, release-all, shadow and autonomous modes. Validated against an in-process fake Slurm that speaks the
   recorded formats and against the cloud's real code through a shared contract file; the real-Slurm run is step 5.
4. **Measurement and dashboard: DONE 2026-09-21** (plan: `docs/superpowers/plans/2026-09-21-demo-and-live-dashboard.md`):
   baseline and savings per managed job, site picker, job states, mode and kill switch, plus a five-minute time-lapse
   demo (`e2e/demo.py`) that runs the real Slurm, agent and cloud logic with a compressed tariff clock.
5. **End-to-end proof: DONE 2026-09-20** (plan `docs/superpowers/plans/2026-09-20-end-to-end-proof.md`, reports
   `docs/superpowers/e2e/2026-09-20-end-to-end-fast-report.md` and `...-full-report.md`). Fast mode: 28 of 28 checks. Full
   mode (through a real top-of-hour): 33 of 33 checks; the real cloud's own decision was carried to real starts with the
   agent and the cloud stopped (+11 s, +12 s, +32 s after the set time, never early). The run found one real agent bug
   (a pending array row made `sacct` refuse the whole call, so every later cycle failed); it is fixed with a test.

## 16. Risks and things to verify first

**Verified by Spike 0** (a real Slurm 26.05.2; details and evidence in the findings file):
- Setting `StartTime` on a pending job holds it and Slurm starts it on time, never early, even while GPUs are free
  (lag 6 s, 20 s, 31 s; release with `StartTime=now` within 2 s). **PASS.**
- The decision survives a controller restart. **PASS.**
- `squeue --start` is a usable baseline only with guards (`N/A` at first; a bogus +1 year when running jobs have no
  limit). Handled in section 6.
- GPU counts are in accounting only if the cluster tracks `gres/gpu`; they are always readable from the live view.
  Handled in section 6.
- Per-job energy is not testable in a container; measured power stays deferred.
- A user's own changes are detectable from the agent's own record; the `Comment` marker is weak.
- An Operator account can defer jobs but can also cancel them; Slurm has no defer-only permission. Handled in section 9.

**Still to verify, on a real cluster:** typed GPUs (`gres/gpu:a100:4`), tuned scheduler intervals, priority weights,
preemption, partition limits, and older Slurm versions (for example `--json` support). The first customer must repeat
the start-accuracy and restart-survival tests on their own cluster.

**Confirmed on a real Slurm 26.05.2 (2026-09-21):** the agent's exact `squeue -o` field set with epoch times, `sacct -S
now-14days`, `--gpus=N` style requests (GPU count read correctly), array ids as `<id>_[1-2]`, a user's `--begin` showing
as `BeginTime`, and the "job no longer known" answer. The five-minute demo also showed real jobs held by a set start time
while every GPU was free, and started (4 s and 44 s after their set time on the default scheduler settings) with the agent
and the cloud stopped. The success-criteria run (`e2e/run.py`, fast and full modes) has since been run on the same lab; see
step 5 in section 15. It confirmed the three parser assumptions on recorded real output (`agent/tests/fixtures/real/`):
the `squeue -o` field set with epoch times, `sacct -S now-14days`, and `--gpus=N` as `TresPerJob=gres/gpu:N`. Its tariff is
synthetic (peak until a top-of-hour, then solar); the real MSEDCL tariff was verified separately against the MERC order.

**About the demo:** its tariff clock is compressed (every two minutes count as one tariff hour) by patches applied in the
demo cloud's process only (`e2e/timelapse.py`); it is a presentation aid, not a test of the real tariff.

Other risks: modeled savings may disappoint on long-training workloads (0.2% on the real Helios sample); a company on
fixed-price or self-generated power gains little; only MSEDCL is verified; agent packaging, upgrades and key rotation
need a plan before a real customer; time-zone and clock skew are avoided by using UTC everywhere.

## 17. Success criteria for the first version

1. In shadow mode the agent reports jobs and the cloud logs plans, and **nothing in Slurm is modified**. *(Verified on a real Slurm 2026-09-20.)*
2. In autonomous mode, flex jobs start inside the planned window without any human action; non-flex jobs are untouched. *(Verified on a real Slurm 2026-09-20; arrays and dependent jobs are skipped.)*
3. Killing the agent and the cloud after decisions does not stop or delay any job. *(Verified on a real Slurm 2026-09-20, with the real cloud's own decision.)*
4. `release_all` restores immediate start for every deferred job. *(Verified on a real Slurm 2026-09-20.)*
5. A job a user changed themselves is no longer managed. *(Verified on a real Slurm 2026-09-20.)*
6. The dashboard shows each job's state and the modeled saving, and the figures match a hand calculation. **Verified
   2026-09-21:** the Live cluster page shows each job's state, and the measured saving matches a hand calculation
   (`backend/tests/test_measure.py`: 8 GPUs x 1.25 kW x 1 h, peak to solar, saves ₹33.76 of ₹105.50).
