# e2e: a real Slurm, the real agent, the real cloud

This folder holds the Docker Slurm lab, the helpers that run the agent and an isolated copy of the cloud against it, and the
five-minute demo.

## What you need

- Docker Desktop running, and the local images `slurm-docker-cluster:26.05.2` and `mariadb:12` (already pulled by the
  earlier Slurm spike; nothing here downloads anything).
- A copy of `github.com/giovtorres/slurm-docker-cluster` in `e2e/slurm-lab/upstream/` (not committed; copy it from where
  the spike cloned it, without its `.git` folder).
- The backend venv (`backend/.venv`), the agent venv (`agent/.venv`) and `TEST_DATABASE_URL` in `~/.wattshift/.env`. The
  cloud used here runs on the **test** database, never on the normal one.

## Commands

| Command | What it does |
|---|---|
| `python e2e/lab.py up` | start and configure the Slurm cluster (two nodes, four fake GPUs each, users `alice`, `bob`, and an Operator `wsagent`, strict `PrivateData`) |
| `python e2e/lab.py smoke` | run one small GPU job and print its accounting row |
| `python e2e/lab.py down` | remove the containers and volumes (the images stay) |
| `python e2e/demo.py` | the narrated five-minute demo (see `docs/demo/storyboard.md`) |
| `python e2e/run.py` | the end-to-end proof, **fast** mode (about 10 to 17 minutes, any time of day): shadow, release-all, deferral by the real cloud, owner override, Slurm holding jobs with the agent and a stand-in cloud stopped, reconcile. Writes `docs/superpowers/e2e/<date>-end-to-end-fast-report.md` |
| `python e2e/run.py --mode full` | the same, but idles until 13 minutes before a top-of-hour and then carries the **real cloud's own decision** to a real start with the agent and the cloud stopped (`--dry-run` prints the schedule; `--now` skips the idle wait). About 35 minutes in total when started at a bad moment |
| `python e2e/capture_formats.py` | record what a real Slurm prints for the agent's commands, as test fixtures |
| `python -m pytest e2e/` | the pure tests (tariff helpers, the stand-in cloud, the time-lapse patches) |

Use the backend venv's Python for all of them.

## What is real and what is not

Real: Slurm, the agent (unmodified, from its own venv), and the cloud's own planning, measurement and sync code.
Synthetic: the tariff (`cloud.hour_rules`) and constant electricity prices, so a cheap window can be arranged. In the
demo the tariff clock is also compressed (`timelapse.py`: every two minutes count as one tariff hour), patched into the
demo cloud's process only.
