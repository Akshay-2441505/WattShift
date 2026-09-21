# Wattshift agent

A small program that runs inside your network next to your Slurm cluster. It makes outbound HTTPS calls only, and the
only thing it ever changes in Slurm is the **start time of jobs your own rules allow**, moving them to cheaper
electricity hours. Every start time it sets is enforced by Slurm itself, so if the agent or the Wattshift cloud goes
away, your jobs still start when Slurm says.

## Install

Python 3.10 or newer. One dependency (PyYAML).

```bash
python -m venv /opt/wattshift
/opt/wattshift/bin/pip install .
```

(On Windows for development: `python -m venv .venv`, then `.venv\Scripts\pip install -e ".[dev]"`.)

## The Slurm account it needs

Give it an account with **Operator** level (`sacctmgr modify user <name> set adminlevel=Operator`). Two reasons: when the
cluster sets `PrivateData`, a plain user cannot see other users' pending jobs, and only an Operator can change another
user's start time.

Be aware of what that means: **Slurm has no permission that allows only deferring a job**. An Operator can also cancel
jobs. That is why the agent restricts itself to a short allow-list (below), writes every command to an audit log you can
read, and starts in shadow mode so you can see what it would do before it does anything.

## Exactly what the agent runs

| Purpose | Command |
|---|---|
| List jobs | `squeue -h -t PENDING,RUNNING -o "%i\|%T\|%u\|%a\|%q\|%P\|%V\|%S\|%r\|%j"` |
| Read one job | `scontrol show job <id>` (only for jobs your rules cover, plus jobs it deferred) |
| Finished jobs | `sacct -j <ids> -P -X -n -S now-14days -o JobID,State,Start,End,ElapsedRaw,AllocTRES` |
| Defer a job | `scontrol update JobId=<id> StartTime=<UTC time>` (autonomous mode only) |
| Release a job | `scontrol update JobId=<id> StartTime=now` |

Nothing else is ever executed: no `scancel`, no hold, no change to any other job field, no shell. Every command runs
with `TZ=UTC` and `SLURM_TIME_FORMAT=%s` and is written to the audit log with its exit code.

## Modes

- **shadow** (the default): the agent reads and reports. It has no code path that sets a future start time. The one
  thing it may still do is put a job it deferred earlier back to "start now" (for example right after you switch from
  autonomous back to shadow).
- **autonomous**: the agent also applies the cloud's start times. Both the agent's `mode` and the site's setting in the
  cloud must say autonomous before anything is deferred.

## First run

```bash
wattshift-agent -c agent.yaml check       # tests Slurm access, the time format and the cloud connection
wattshift-agent -c agent.yaml run --once  # one cycle
wattshift-agent -c agent.yaml run         # the loop (run it under a service manager)
```

A minimal systemd unit:

```ini
[Unit]
Description=Wattshift agent
After=network-online.target

[Service]
User=wattshift
Environment=WATTSHIFT_SITE_KEY=<your key>
ExecStart=/opt/wattshift/bin/wattshift-agent -c /etc/wattshift/agent.yaml run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## The kill switch

```bash
wattshift-agent -c agent.yaml release-all
```

Sets every job the agent deferred back to "start now". It works without the cloud, and the next sync tells the cloud to
stop planning for this site. Stopping the agent has a different effect: every start time already set stays in force and
Slurm honours it.

## What leaves your network

Only these facts about the jobs your rules cover: job id, state, submit/start/end times, GPU count, time limit, your
`max_wait`, Slurm's predicted start, and two flags (changed by its owner, skipped). Never user names, accounts, job names,
command lines or scripts. Rules are evaluated inside your network.

## Troubleshooting

- `check` says times are not epoch seconds: `SLURM_TIME_FORMAT=%s` is not reaching `squeue`. If you use `slurm.prefix`
  (ssh, docker exec), pass it through there.
- The audit log (`audit_file`) has one JSON line per Slurm command and per decision.
- `wattshift-agent status` shows what the agent has recorded: tracked jobs by state, jobs it deferred, jobs skipped
  (arrays, dependencies, requeues) and jobs their owners changed.
