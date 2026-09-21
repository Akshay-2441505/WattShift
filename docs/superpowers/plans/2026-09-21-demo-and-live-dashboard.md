# Live dashboard and the 5-minute demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) The cloud measures what deferral really saved from Slurm's real start and end times, and the dashboard gets a **Live cluster** view of a customer site: each job's state, when it would have started versus when it will, the modeled saving, the mode, the kill switch, and an activity feed. (2) A **time-lapse demo mode** that runs the real chain (real Slurm, real agent, real cloud) in about five minutes by compressing the tariff clock, with a narrated script, and a backup pack (screenshots and a transcript) in case the live demo fails.

**Architecture:** Cloud: a small `measure.py` prices a finished job on its real runtime (baseline and actual on the same minutes) and a `site_views.py` builds one read model per site (`GET /sites`, `GET /sites/{id}/view`). Frontend: a new `#/live` tab polling that one endpoint every 2 seconds, with pure layout functions unit-tested and a hand-drawn SVG "shift timeline" as the signature visual. Demo: a launcher starts the real cloud with a **time-lapse tariff** (every 2 minutes counts as one tariff "hour", allocator blocks of 1 minute, no start-time spread), all patched in that process only; a script drives the real Slurm lab and the real agent through a narrated sequence.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy (backend), React 19 + TypeScript + Tailwind v4 + vitest (frontend, existing), Docker Slurm lab from the end-to-end plan. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md` sections 10 (measuring savings), 13 (dashboard) and 17 criterion 6. Builds on `docs/superpowers/plans/2026-09-20-cloud-core.md`, `2026-09-20-slurm-agent.md` and `2026-09-20-end-to-end-proof.md` (Tasks 1 and 3 of that plan supply the lab and the process helpers this demo reuses).

## Decisions made (so nobody re-litigates them)

- **Measured saving definition** (spec section 10): for a job the agent deferred and that has finished, `baseline_cost` and `actual_cost` are both billed on the **same real runtime** (Slurm's start to end, whole minutes, at least 1): the baseline at `baseline_start` (where it would have started), the actual at the real start. `saved = baseline - actual`, negative values kept. Power = GPUs x the site's kW per GPU. Before a job finishes its `baseline_cost` and `planned_cost` are estimates from the time limit; on finishing, `baseline_cost` is replaced by the measured one. Jobs the agent never deferred have no measured saving.
- **"Would save" (potential)** shown for planned deferrals not yet finished: `baseline_cost - planned_cost`, always labelled a plan/estimate, never added to the measured total.
- **One read endpoint per site** (`GET /sites/{id}/view`) so the page makes one request per poll. Reads are open in development like the existing read endpoints; Phase 8 gates them with the rest.
- **Operator controls on the page** (mode toggle, release-all) call the existing `POST /sites/{id}/mode` and `POST /sites/{id}/release-all`, with the same `X-API-Key` handling the Submit job dialog already has (the key field appears only after a 401).
- **Time-lapse is a demo device, and the demo says so.** Patched in the demo cloud process only: `tod_zone` maps time to a pseudo-hour (`floor(epoch / 120 s) mod 24`), allocator block length 1 minute, start-time spread 0. Production code is not changed. Real Slurm, real agent, real cloud logic otherwise.
- **The demo needs the end-to-end plan's lab code** (`e2e/lab.py`, `e2e/cloud.py`, `e2e/agentproc.py`). Build Tasks 1 and 3 of that plan first (Task 0 below).
- **No commits** (the repo has none yet); commit only when the user asks. Existing suites must stay green: backend 247, agent 182, frontend 31.

## File structure

| File | Responsibility |
|---|---|
| `backend/app/measure.py` (create) | `finalize()`: price a finished, deferred job on its real runtime |
| `backend/app/agent_sync.py` (modify) | call `measure.finalize` for the jobs a sync touched |
| `backend/app/site_views.py` (create) | `display_state()`, `describe()`, `zone_segments()`, `build_view()` |
| `backend/app/main.py` (modify) | `GET /sites`, `GET /sites/{id}/view` |
| `backend/tests/test_measure.py`, `test_site_views.py`, `test_site_api.py` (create) | tests |
| `frontend/src/types.ts`, `api.ts` (modify) | `SiteView` types, `getSites`, `getSiteView`, `setSiteMode`, `releaseAll` |
| `frontend/src/lib/live.ts`, `live.test.ts` (create) | pure helpers: state labels, timeline layout, countdown |
| `frontend/src/useSitePoll.ts` (create) | 2-second poll of one site |
| `frontend/src/components/live/*.tsx` (create) | header/controls, summary, shift timeline, jobs table, activity feed |
| `frontend/src/views/Live.tsx`, `App.tsx` (create/modify) | the `#/live` tab |
| `e2e/timelapse.py`, `e2e/demo_cloud_main.py` (create) | time-lapse patches and the demo cloud launcher |
| `e2e/demo.py` (create) | the narrated five-minute demo |
| `e2e/test_timelapse.py` (create) | pure tests for the time-lapse helpers |
| `docs/demo/` (create) | storyboard, backup screenshots, transcript |

---

### Task 0: Prerequisite, the lab and the process helpers

**Files:** those of Tasks 1 and 3 of `docs/superpowers/plans/2026-09-20-end-to-end-proof.md` (`e2e/lab.py`, `e2e/cloud.py`, `e2e/cloud_main.py`, `e2e/stubcloud.py`, `e2e/agentproc.py`, `e2e/conftest.py`, `e2e/test_helpers.py`, `e2e/README.md`, `.gitignore` lines).

- [ ] **Step 1:** Execute Task 1 (the lab as code) and Task 3 (the cloud side) of the end-to-end plan exactly as written there, including bringing the Docker lab up and running its smoke job. Task 2 of that plan (real Slurm format fixtures) is strongly recommended too: the demo relies on the same parsers, and it is a 10-minute step.
- [ ] **Step 2:** Confirm: `.\backend\.venv\Scripts\python e2e\lab.py smoke` prints a `COMPLETED` row, and `.\backend\.venv\Scripts\python -m pytest e2e/test_helpers.py -q` passes.

This task adds no code of its own. If Docker is not running or the lab cannot be brought up, stop and tell the user.

---

### Task 1: Measure what deferral really saved

**Files:**
- Create: `backend/app/measure.py`, `backend/tests/test_measure.py`
- Modify: `backend/app/agent_sync.py`

**Interfaces:**
- Consumes: `ManagedJob`, `Site` (existing), `bill_cost` (`app.savings`), `get_tariff` (`app.catalogue`), `audit` (`app.audit`), `IST`.
- Produces: `measure.FINISHED` (tuple of terminal states), `measure.elapsed_minutes(job) -> int`, `measure.finalize(session, site, job, now) -> bool` (True when it wrote a measurement). `process_sync` calls it for every job the request touched, so the cloud fills `actual_cost` and `saved` by itself when the agent reports a finished job.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_measure.py`:

```python
import uuid
from datetime import timedelta

import pytest

from app import agent_sync, measure, sites
from app.catalogue import seed_catalogue
from app.models import AuditLog, ManagedJob
from app.seed import seed_tod
from tests.helpers import NOW, add_prices, evening_to_next_noon

SOLAR_START = NOW + timedelta(hours=17)  # Thu 2026-07-02 12:00 IST: solar, x0.85 in July. NOW is 19:00 IST: peak, x1.25.


@pytest.fixture()
def world(session):
    seed_tod(session)
    seed_catalogue(session)
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8, mode="autonomous")  # 1.25 kW per GPU
    return session, site


def job(session, site, **kw):
    base = dict(
        id=uuid.uuid4(), site_id=site.id, ref="1", state="COMPLETED", plan_status="planned", first_seen=NOW, submit_time=NOW,
        gpus=8, time_limit_min=90, max_wait_min=1440, baseline_start=NOW, applied_start=SOLAR_START,
        actual_start=SOLAR_START, actual_end=SOLAR_START + timedelta(minutes=60),
    )
    j = ManagedJob(**{**base, **kw})
    session.add(j)
    session.flush()
    return j


def test_the_saving_matches_a_hand_calculation(world):
    session, site = world
    j = job(session, site)  # 8 GPUs x 1.25 kW = 10 kW for 60 min
    assert measure.finalize(session, site, j, NOW) is True
    assert float(j.baseline_cost) == pytest.approx(10 * 8.44 * 1.25)  # 105.50 at peak
    assert float(j.actual_cost) == pytest.approx(10 * 8.44 * 0.85)  # 71.74 at solar
    assert float(j.saved) == pytest.approx(33.76)


def test_baseline_and_actual_use_the_real_runtime_not_the_time_limit(world):
    session, site = world
    j = job(session, site, actual_end=SOLAR_START + timedelta(minutes=30))  # limit says 90, it ran 30
    measure.finalize(session, site, j, NOW)
    assert float(j.actual_cost) == pytest.approx(5 * 8.44 * 0.85)
    assert float(j.baseline_cost) == pytest.approx(5 * 8.44 * 1.25)


def test_a_negative_saving_is_kept(world):
    session, site = world
    j = job(session, site, baseline_start=SOLAR_START, actual_start=NOW, actual_end=NOW + timedelta(minutes=60), applied_start=NOW)
    measure.finalize(session, site, j, NOW)
    assert float(j.saved) == pytest.approx(10 * 8.44 * (0.85 - 1.25))
    assert float(j.saved) < 0


@pytest.mark.parametrize(
    "kw",
    [
        {"applied_start": None},  # we never deferred it: nothing of ours to measure
        {"state": "RUNNING", "actual_end": None},
        {"actual_start": None},  # cancelled while pending
        {"baseline_start": None},
    ],
)
def test_jobs_we_cannot_honestly_measure_are_left_alone(world, kw):
    session, site = world
    j = job(session, site, **kw)
    assert measure.finalize(session, site, j, NOW) is False
    assert j.actual_cost is None and j.saved is None


def test_a_measurement_is_written_once(world):
    session, site = world
    j = job(session, site)
    assert measure.finalize(session, site, j, NOW) is True
    assert measure.finalize(session, site, j, NOW + timedelta(seconds=30)) is False
    assert session.query(AuditLog).filter_by(event="measured").count() == 1


def test_a_short_job_counts_at_least_one_minute(world):
    session, site = world
    assert measure.elapsed_minutes(job(session, site, actual_end=SOLAR_START + timedelta(seconds=5))) == 1


def test_a_finished_report_from_the_agent_is_priced_by_the_cloud_itself(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon())
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8, mode="autonomous")
    fact = {"ref": "9", "state": "PENDING", "submit_time": NOW.isoformat(), "gpus": 8, "time_limit_min": 90, "max_wait_min": 17 * 60}
    body = lambda **kw: agent_sync.SyncIn.model_validate({"agent_version": "0", "mode": "autonomous", "sent_at": NOW.isoformat(), **kw})  # noqa: E731
    out = agent_sync.process_sync(session, site, body(jobs=[fact]), NOW)
    start = out.decisions[0].start_at
    done = {**fact, "state": "COMPLETED", "start_time": start.isoformat(), "end_time": (start + timedelta(minutes=60)).isoformat()}
    agent_sync.process_sync(session, site, body(jobs=[done], applied=[{"ref": "9", "start_at": start.isoformat(), "ok": True}]), NOW + timedelta(hours=17))
    session.expire_all()
    j = session.query(ManagedJob).filter_by(ref="9").one()
    assert j.saved is not None and float(j.saved) > 0 and float(j.actual_cost) < float(j.baseline_cost)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_measure.py -q -p no:cacheprovider`
Expected: FAIL, `ImportError: cannot import name 'measure'`.

- [ ] **Step 3: Write `measure.py`**

Create `backend/app/measure.py`:

```python
"""Measuring what deferral saved, from what Slurm really did (spec section 10). Modeled power, real times."""
from datetime import datetime

from sqlalchemy.orm import Session

from app import audit
from app.catalogue import get_tariff
from app.models import ManagedJob, Site
from app.savings import bill_cost
from app.tariff import IST

FINISHED = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OTHER")


def elapsed_minutes(job: ManagedJob) -> int:
    """The job's real runtime in whole minutes (at least 1), from Slurm's start and end."""
    return max(1, round((job.actual_end - job.actual_start).total_seconds() / 60))


def finalize(session: Session, site: Site, job: ManagedJob, now: datetime) -> bool:
    """Price a finished job that WE deferred: baseline (where it would have started) and actual (where it did) are both
    billed on the same real runtime, so the difference is purely the tariff zone. Replaces the time-limit estimate in
    baseline_cost. Negative savings are kept. Returns True only when it wrote a measurement (once per job)."""
    if job.state not in FINISHED or job.applied_start is None or job.actual_cost is not None:
        return False
    if not (job.actual_start and job.actual_end and job.baseline_start):
        return False
    try:
        tariff = get_tariff(session, site.utility, site.tariff_category, job.actual_start.astimezone(IST).date())
    except LookupError:
        audit.log_throttled(session, site.id, now, "planner", "no_tariff_for_measurement", ref=job.ref)
        return False
    minutes = elapsed_minutes(job)
    power = max(job.gpus or 1, 1) * float(site.kw_per_gpu)
    job.baseline_cost = bill_cost(job.baseline_start, minutes, power, tariff.rules, tariff.base_rate)
    job.actual_cost = bill_cost(job.actual_start, minutes, power, tariff.rules, tariff.base_rate)
    job.saved = round(float(job.baseline_cost) - float(job.actual_cost), 4)
    audit.log(session, site.id, now, "planner", "measured", ref=job.ref, saved=float(job.saved), minutes=minutes)
    return True
```

- [ ] **Step 4: Call it from the sync**

In `backend/app/agent_sync.py` change the import line `from app import audit, planner, sites` to `from app import audit, measure, planner, sites`, and in `process_sync` add these two lines directly after `_apply_reports(session, site, body, now, jobs)`:

```python
    for j in jobs.values():  # price any job that just finished (real start and end, spec section 10)
        measure.finalize(session, site, j, now)
```

- [ ] **Step 5: Run them and the existing sync tests**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_measure.py tests/test_agent_sync.py tests/test_agent_api.py tests/test_agent_contract.py -q -p no:cacheprovider`
Expected: all pass. If `test_a_finished_report_from_the_agent_is_priced_by_the_cloud_itself` fails on `start`, print `out.decisions`: the job may not have been deferred (prices from `evening_to_next_noon` make next-day solar cheaper, so it should be).

- [ ] **Step 6: Mutation-check**

Change `job.saved = round(float(job.baseline_cost) - float(job.actual_cost), 4)` to `abs(...)` and confirm `test_a_negative_saving_is_kept` fails; change `job.applied_start is None` in the guard to `False` and confirm the `applied_start: None` parametrized case fails. Restore both.

---

### Task 2: The site read model and its endpoints

**Files:**
- Create: `backend/app/site_views.py`, `backend/tests/test_site_views.py`, `backend/tests/test_site_api.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `ManagedJob`, `Site`, `Company`, `AuditLog`, `get_tariff`, `Tariff`, `tariff.tod_zone` (called through the module, `tariff_mod.tod_zone(...)`, so the demo's time-lapse patch takes effect), `IST`.
- Produces:
  - `site_views.display_state(job) -> str`, one of `seen, would_hold, held, runs_normally, no_window, skipped, owner_changed, left_alone, released, running, done, ended`
  - `site_views.describe(audit_row) -> str` (one plain-language line per audit event)
  - `site_views.zone_segments(tariff, start, end) -> list[{start, end, zone}]` (1-minute steps for windows up to 3 hours, else 15-minute steps; adjacent equal zones merged)
  - `site_views.build_view(session, site, now) -> dict` with keys `site`, `now`, `summary`, `jobs`, `activity`, `zones`
  - `GET /sites` (list of `{id, company, name, mode, release_all, gpus, last_seen_at, agent_version}`) and `GET /sites/{site_id}/view` (404 for an unknown site)

`summary` = `{saved, baseline, pct_saved, jobs_measured, potential, counts}`. `saved` and `baseline` add up only measured jobs (`saved` not null). `potential` = sum of `baseline_cost - planned_cost` over pending jobs with a planned deferral (an estimate, never added to `saved`). `counts` maps each display state to a number.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_site_views.py`:

```python
import uuid
from datetime import datetime, timedelta

import pytest

from app import audit, site_views, sites
from app.catalogue import get_tariff, seed_catalogue
from app.models import AuditLog, ManagedJob
from app.seed import seed_tod
from app.tariff import IST
from tests.helpers import NOW

T = NOW + timedelta(hours=17)  # Thu 12:00 IST, solar


def j(**kw):
    base = dict(state="PENDING", plan_status="pending", note=None, planned_start=None, applied_start=None)
    return ManagedJob(**{**base, **kw})


@pytest.mark.parametrize(
    "job, want",
    [
        (j(), "seen"),
        (j(plan_status="planned", planned_start=T), "would_hold"),  # planned but the agent has not confirmed it (or shadow mode)
        (j(plan_status="planned", planned_start=T, applied_start=T), "held"),
        (j(plan_status="planned"), "runs_normally"),  # not worth moving: the bill would not be lower
        (j(plan_status="unplaceable"), "no_window"),
        (j(plan_status="skipped", note="array"), "skipped"),
        (j(plan_status="abandoned", note="user_changed"), "owner_changed"),
        (j(plan_status="abandoned", note="apply_failed: nope"), "left_alone"),
        (j(plan_status="released"), "released"),
        (j(state="RUNNING", plan_status="planned"), "running"),
        (j(state="COMPLETED"), "done"),
        (j(state="FAILED"), "ended"),
        (j(state="CANCELLED"), "ended"),
    ],
)
def test_display_state(job, want):
    assert site_views.display_state(job) == want


def test_activity_lines_are_plain_language():
    row = lambda event, ref=None, **d: AuditLog(event=event, ref=ref, detail=d or None)  # noqa: E731
    assert site_views.describe(row("job_seen", "48211")) == "Saw job 48211"
    assert site_views.describe(row("skipped", "50_[1-3]", reason="array")) == "Skipping job 50_[1-3] (array)"
    assert site_views.describe(row("mode_changed", mode="autonomous")) == "Mode changed to autonomous"
    assert "Kill switch on" in site_views.describe(row("release_all_on"))
    assert "site is in shadow" in site_views.describe(row("mode_mismatch", site_mode="shadow", agent_mode="autonomous"))
    assert "agent is in shadow" in site_views.describe(row("mode_mismatch", site_mode="autonomous", agent_mode="shadow"))
    assert site_views.describe(row("something_new")) == "something new"  # an unknown event is still readable


def test_zone_segments_merge_and_split_at_the_tariff_boundary(session):
    seed_tod(session)
    seed_catalogue(session)
    tariff = get_tariff(session, "MSEDCL", "HT-I(A)", datetime(2026, 7, 1).date())
    start = datetime(2026, 7, 1, 16, 55, tzinfo=IST)
    segs = site_views.zone_segments(tariff, start, start + timedelta(minutes=10))  # 1-minute steps
    assert [(s["zone"], s["start"], s["end"]) for s in segs] == [
        ("solar", start, start + timedelta(minutes=5)),
        ("peak", start + timedelta(minutes=5), start + timedelta(minutes=10)),
    ]
    day = site_views.zone_segments(tariff, datetime(2026, 7, 1, 0, 0, tzinfo=IST), datetime(2026, 7, 2, 0, 0, tzinfo=IST))  # 15-minute steps
    assert [s["zone"] for s in day] == ["baseline", "solar", "peak"]


def make(session, site, ref, **kw):
    base = dict(id=uuid.uuid4(), site_id=site.id, ref=ref, state="PENDING", plan_status="pending", first_seen=NOW, submit_time=NOW, gpus=4,
                time_limit_min=60, max_wait_min=1440)
    row = ManagedJob(**{**base, **kw})
    session.add(row)
    session.flush()
    return row


def test_the_view_separates_measured_savings_from_the_plan(session):
    seed_tod(session)
    seed_catalogue(session)
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8, mode="autonomous")
    make(session, site, "1", state="COMPLETED", plan_status="planned", applied_start=T, planned_start=T, actual_start=T,
         actual_end=T + timedelta(hours=1), baseline_start=NOW, baseline_cost=105.5, planned_cost=71.74, actual_cost=71.74, saved=33.76)
    make(session, site, "2", plan_status="planned", planned_start=T, applied_start=T, baseline_start=NOW, baseline_cost=50.0, planned_cost=30.0)
    make(session, site, "3", plan_status="skipped", note="array")
    audit.log(session, site.id, NOW, "agent", "job_seen", ref="2")
    audit.log(session, site.id, NOW + timedelta(seconds=5), "planner", "decision", ref="2")
    v = site_views.build_view(session, site, NOW)
    assert v["site"]["name"] == "Pune-1" and v["site"]["mode"] == "autonomous" and v["site"]["company"] == "Acme"
    s = v["summary"]
    assert (s["saved"], s["baseline"], s["jobs_measured"]) == (33.76, 105.5, 1) and s["pct_saved"] == 32.0
    assert s["potential"] == 20.0  # only job 2: pending with a planned deferral; job 1 is already measured
    assert s["counts"] == {"done": 1, "held": 1, "skipped": 1}
    assert {r["ref"]: r["display"] for r in v["jobs"]} == {"1": "done", "2": "held", "3": "skipped"}
    assert [a["event"] for a in v["activity"]] == ["decision", "job_seen"]  # newest first
    assert v["zones"] and {"start", "end", "zone"} <= set(v["zones"][0])


def test_the_view_still_works_when_the_tariff_has_expired(session):
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=8)  # no catalogue entry at all
    v = site_views.build_view(session, site, NOW)
    assert v["zones"] == [] and v["jobs"] == [] and v["summary"]["saved"] == 0 and v["summary"]["pct_saved"] is None
```

Create `backend/tests/test_site_api.py`:

```python
import itertools
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import main, sites
from app.catalogue import seed_catalogue
from app.seed import seed_tod
from tests.helpers import NOW


@pytest.fixture()
def env(session):
    seed_tod(session)
    seed_catalogue(session)
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=64)
    ticks = itertools.count()
    main.app.dependency_overrides[main.get_session] = lambda: session
    main.app.dependency_overrides[main.get_now] = lambda: NOW + timedelta(seconds=next(ticks))
    yield TestClient(main.app), site
    main.app.dependency_overrides.clear()


def test_sites_are_listed(env):
    client, site = env
    out = client.get("/sites").json()
    assert out == [{"id": str(site.id), "company": "Acme", "name": "Pune-1", "mode": "shadow", "release_all": False, "gpus": 64,
                    "last_seen_at": None, "agent_version": None}]


def test_the_view_has_everything_the_page_needs(env):
    client, site = env
    r = client.get(f"/sites/{site.id}/view")
    assert r.status_code == 200
    v = r.json()
    assert set(v) == {"site", "now", "summary", "jobs", "activity", "zones"}
    assert v["site"]["id"] == str(site.id) and v["summary"]["counts"] == {}


def test_an_unknown_site_is_404(env):
    client, _ = env
    assert client.get(f"/sites/{uuid.uuid4()}/view").status_code == 404
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_site_views.py tests/test_site_api.py -q -p no:cacheprovider`
Expected: FAIL, `ImportError: cannot import name 'site_views'`.

- [ ] **Step 3: Write `site_views.py`**

Create `backend/app/site_views.py`:

```python
"""Read models for the Live cluster dashboard: everything one site's page needs, in one call."""
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import tariff as tariff_mod  # called as tariff_mod.tod_zone so a demo's time-lapse patch is picked up
from app.catalogue import Tariff, get_tariff
from app.measure import FINISHED
from app.models import AuditLog, Company, ManagedJob, Site
from app.tariff import IST


def display_state(j: ManagedJob) -> str:
    """The one word the dashboard shows for a job (the frontend turns it into a label and a colour)."""
    if j.state in FINISHED:
        return "done" if j.state == "COMPLETED" else "ended"
    if j.state == "RUNNING":
        return "running"
    if j.plan_status == "released":
        return "released"
    if j.plan_status == "skipped":
        return "skipped"
    if j.plan_status == "abandoned":
        return "owner_changed" if j.note == "user_changed" else "left_alone"
    if j.plan_status == "unplaceable":
        return "no_window"
    if j.plan_status == "planned":
        if j.planned_start is None:
            return "runs_normally"
        return "held" if j.applied_start == j.planned_start else "would_hold"
    return "seen"


def describe(a: AuditLog) -> str:
    d = a.detail or {}
    job = f"job {a.ref}" if a.ref else "a job"
    lines = {
        "job_seen": f"Saw {job}",
        "decision": f"Decided a start time for {job}",
        "applied": f"Set the start time of {job} in Slurm",
        "apply_failed": f"Could not set the start time of {job}; leaving it alone",
        "override": f"The owner changed {job}; no longer managing it",
        "skipped": f"Skipping {job} ({d.get('reason', 'not supported')})",
        "unplaceable": f"No cheaper window for {job}; it runs as normal",
        "released": f"Released {job} to start now",
        "measured": f"{job.capitalize()} finished; saving measured",
        "release_all_on": "Kill switch on: releasing every held job",
        "release_all_off": "Kill switch off",
        "mode_changed": f"Mode changed to {d.get('mode')}",
        "agent_mode": f"Agent connected in {d.get('mode')} mode",
        "mode_mismatch": (
            "The site is in shadow mode: plans are recorded, nothing is sent to the agent" if d.get("site_mode") == "shadow"
            else "The agent is in shadow mode: it reports but never changes Slurm"
        ),
        "shadow_violation": "A shadow agent tried to change Slurm",
        "no_tariff": "No valid tariff: planning paused",
    }
    return lines.get(a.event, a.event.replace("_", " "))


def zone_segments(tariff: Tariff, start: datetime, end: datetime) -> list[dict]:
    """Tariff zones over [start, end): 1-minute steps up to 3 hours (the time-lapse demo has zones minutes wide), else 15."""
    step = timedelta(minutes=1 if end - start <= timedelta(hours=3) else 15)
    t = start.replace(second=0, microsecond=0)
    segs: list[dict] = []
    while t < end:
        zone = tariff_mod.tod_zone(t, tariff.rules).zone
        if segs and segs[-1]["zone"] == zone:
            segs[-1]["end"] = t + step
        else:
            segs.append({"start": t, "end": t + step, "zone": zone})
        t += step
    return segs


def _money(x) -> float | None:
    return None if x is None else float(x)


def _job_row(j: ManagedJob) -> dict:
    return {
        "ref": j.ref, "state": j.state, "plan_status": j.plan_status, "display": display_state(j), "note": j.note, "gpus": j.gpus,
        "time_limit_min": j.time_limit_min, "max_wait_min": j.max_wait_min, "submit_time": j.submit_time,
        "baseline_start": j.baseline_start, "planned_start": j.planned_start, "applied_start": j.applied_start,
        "actual_start": j.actual_start, "actual_end": j.actual_end, "baseline_cost": _money(j.baseline_cost),
        "planned_cost": _money(j.planned_cost), "actual_cost": _money(j.actual_cost), "saved": _money(j.saved),
    }


def build_view(session: Session, site: Site, now: datetime) -> dict:
    company = session.get(Company, site.company_id)
    jobs = session.scalars(
        select(ManagedJob).where(ManagedJob.site_id == site.id).order_by(ManagedJob.submit_time.desc(), ManagedJob.ref).limit(200)
    ).all()
    try:
        tariff = get_tariff(session, site.utility, site.tariff_category, now.astimezone(IST).date())
    except LookupError:
        tariff = None

    measured = [j for j in jobs if j.saved is not None]
    saved = sum(float(j.saved) for j in measured)
    baseline = sum(float(j.baseline_cost) for j in measured)
    potential = sum(
        float(j.baseline_cost) - float(j.planned_cost) for j in jobs
        if j.state == "PENDING" and j.plan_status == "planned" and j.planned_start is not None
        and j.baseline_cost is not None and j.planned_cost is not None
    )
    events = session.scalars(select(AuditLog).where(AuditLog.site_id == site.id).order_by(AuditLog.id.desc()).limit(40)).all()

    zones: list[dict] = []
    if tariff is not None:
        marks = [t for j in jobs for t in (j.planned_start, j.applied_start, j.actual_end) if t is not None]
        lo = now - timedelta(minutes=5)
        hi = min(max([now + timedelta(minutes=15), *(m + timedelta(minutes=10) for m in marks)]), now + timedelta(hours=36))
        zones = zone_segments(tariff, lo, hi)

    return {
        "site": {
            "id": str(site.id), "company": company.name, "name": site.name, "mode": site.mode, "release_all": site.release_all,
            "gpus": site.gpus, "last_seen_at": site.last_seen_at, "agent_version": site.agent_version,
            "agent_mode": site.last_agent_mode, "tariff": f"{site.utility} {site.tariff_category}",
        },
        "now": now,
        "summary": {
            "saved": round(saved, 2), "baseline": round(baseline, 2),
            "pct_saved": round(saved / baseline * 100, 2) if baseline else None, "jobs_measured": len(measured),
            "potential": round(potential, 2), "counts": dict(Counter(display_state(j) for j in jobs)),
        },
        "jobs": [_job_row(j) for j in jobs],
        "activity": [{"at": a.at, "actor": a.actor, "event": a.event, "ref": a.ref, "text": describe(a)} for a in events],
        "zones": zones,
    }
```

- [ ] **Step 4: Add the endpoints**

In `backend/app/main.py` change `from app import agent_sync, backtest_runs, clock, db, replay, runner, service, sites` to also import `site_views` (alphabetical: `..., service, site_views, sites`), change `from app.models import Job, SavingsLog, Site` to `from app.models import Company, Job, SavingsLog, Site`, and append at the end of the file:

```python
@app.get("/sites")
def list_sites(session: Session = Depends(get_session)):
    rows = session.execute(select(Site, Company.name).join(Company, Company.id == Site.company_id).order_by(Company.name, Site.name)).all()
    return [
        {"id": str(s.id), "company": c, "name": s.name, "mode": s.mode, "release_all": s.release_all, "gpus": s.gpus,
         "last_seen_at": s.last_seen_at, "agent_version": s.agent_version}
        for s, c in rows
    ]


@app.get("/sites/{site_id}/view")
def site_view(site_id: uuid.UUID, session: Session = Depends(get_session), now: datetime = Depends(get_now)):
    """Everything the Live cluster page shows for one site, in one call."""
    return site_views.build_view(session, _site_or_404(session, site_id), now)
```

- [ ] **Step 5: Run them, then the whole backend suite**

Run: `cd backend; .\.venv\Scripts\python -m pytest tests/test_site_views.py tests/test_site_api.py -q -p no:cacheprovider`, then `.\.venv\Scripts\python -m pytest -q -p no:cacheprovider`
Expected: all pass (247 plus the new tests). If `test_sites_are_listed` differs only in the JSON form of `last_seen_at`, it is `None` here, so the dict must match exactly.

---

### Task 3: The frontend data layer (types, calls, polling, pure layout helpers)

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/api.ts`, `frontend/src/lib/time.ts`
- Create: `frontend/src/lib/live.ts`, `frontend/src/lib/live.test.ts`, `frontend/src/useSitePoll.ts`

**Interfaces:**
- Consumes: the endpoints of Task 2 (`/api/sites`, `/api/sites/{id}/view`) and the existing `POST /sites/{id}/mode` and `/release-all`.
- Produces:
  - types `DisplayState`, `SiteInfo`, `SiteJob`, `SiteView` (below)
  - `api.getSites()`, `api.getSiteView(id)`, `api.setSiteMode(id, mode, apiKey?)`, `api.releaseAll(id, on, apiKey?)` (the last two return `OpResult = { kind: 'ok' } | { kind: 'auth' } | { kind: 'error'; message: string }`)
  - `time.fmtHMS(iso) -> "HH:MM:SS"` (IST)
  - `live.STATE: Record<DisplayState, { label: string; tone: Tone }>`, `live.countdown(nowIso, targetIso)`, `live.jobLine(job)`, `live.agentStatus(view)`, `live.timelineLayout(view)`
  - hooks `useSites(intervalMs = 5000)` and `useSitePoll(siteId, intervalMs = 2000)`, each returning `{ data, error, refresh }`

- [ ] **Step 1: Add the types**

Append to `frontend/src/types.ts`:

```ts
// --- Live cluster (a customer site managed by the agent): backend/app/site_views.py ---
export type DisplayState =
  | 'seen' | 'would_hold' | 'held' | 'runs_normally' | 'no_window' | 'skipped'
  | 'owner_changed' | 'left_alone' | 'released' | 'running' | 'done' | 'ended'

export interface SiteInfo {
  id: string
  company: string
  name: string
  mode: 'shadow' | 'autonomous'
  release_all: boolean
  gpus: number
  last_seen_at: string | null
  agent_version: string | null
}

export interface SiteJob {
  ref: string
  state: string // Slurm's state, normalised: PENDING | RUNNING | COMPLETED | ...
  plan_status: string
  display: DisplayState
  note: string | null
  gpus: number | null
  time_limit_min: number | null
  max_wait_min: number | null
  submit_time: string
  baseline_start: string | null // where it would have started without Wattshift
  planned_start: string | null
  applied_start: string | null // the start time the agent confirmed in Slurm
  actual_start: string | null
  actual_end: string | null
  baseline_cost: number | null // Rs, modeled
  planned_cost: number | null
  actual_cost: number | null
  saved: number | null // measured, only once the job has finished
}

export interface SiteView {
  site: SiteInfo & { agent_mode: string | null; tariff: string }
  now: string
  summary: {
    saved: number // measured, from real start and end times
    baseline: number
    pct_saved: number | null
    jobs_measured: number
    potential: number // the plan for jobs not finished yet: an estimate, never part of `saved`
    counts: Partial<Record<DisplayState, number>>
  }
  jobs: SiteJob[]
  activity: { at: string; actor: string; event: string; ref: string | null; text: string }[]
  zones: { start: string; end: string; zone: Zone }[]
}
```

- [ ] **Step 2: Add the API calls**

In `frontend/src/api.ts` extend the first import to `import type { BacktestRun, BacktestSample, Forecast, Job, Savings, SiteInfo, SiteView, Snapshot, Tariff } from './types'` and append:

```ts
export const getSites = () => get<SiteInfo[]>('/sites')
export const getSiteView = (id: string) => get<SiteView>(`/sites/${id}/view`)

export type OpResult = { kind: 'ok' } | { kind: 'auth' } | { kind: 'error'; message: string }

async function post(path: string, body: unknown, apiKey?: string): Promise<OpResult> {
  try {
    const r = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(apiKey ? { 'X-API-Key': apiKey } : {}) },
      body: JSON.stringify(body),
    })
    if (r.ok) return { kind: 'ok' }
    if (r.status === 401) return { kind: 'auth' }
    return { kind: 'error', message: `The server answered HTTP ${r.status}.` }
  } catch {
    return { kind: 'error', message: 'Could not reach the Wattshift API. Check that it is running, then try again.' }
  }
}

export const setSiteMode = (id: string, mode: 'shadow' | 'autonomous', apiKey?: string) => post(`/sites/${id}/mode`, { mode }, apiKey)
export const releaseAll = (id: string, on: boolean, apiKey?: string) => post(`/sites/${id}/release-all`, { on }, apiKey)
```

In `frontend/src/lib/time.ts` append:

```ts
export const fmtHMS = (iso: string) => {
  const d = ist(iso)
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
}
```

- [ ] **Step 3: Write the failing tests**

Create `frontend/src/lib/live.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { STATE, agentStatus, countdown, jobLine, timelineLayout } from './live'
import type { DisplayState, SiteJob, SiteView } from '../types'

const T0 = Date.UTC(2026, 8, 21, 10, 0, 0) // 15:30:00 IST
const at = (s: number) => new Date(T0 + s * 1000).toISOString()

const job = (over: Partial<SiteJob>): SiteJob => ({
  ref: '1', state: 'PENDING', plan_status: 'planned', display: 'held', note: null, gpus: 4, time_limit_min: 1, max_wait_min: 180,
  submit_time: at(0), baseline_start: null, planned_start: null, applied_start: null, actual_start: null, actual_end: null,
  baseline_cost: null, planned_cost: null, actual_cost: null, saved: null, ...over,
})

const view = (over: Partial<SiteView> = {}, site: Partial<SiteView['site']> = {}): SiteView => ({
  site: { id: 's', company: 'Acme', name: 'Pune-1', mode: 'autonomous', release_all: false, gpus: 8, last_seen_at: at(-5), agent_version: '0.1.0', agent_mode: 'autonomous', tariff: 'X', ...site },
  now: at(0),
  summary: { saved: 0, baseline: 0, pct_saved: null, jobs_measured: 0, potential: 0, counts: {} },
  jobs: [], activity: [],
  zones: [{ start: at(-60), end: at(120), zone: 'peak' }, { start: at(120), end: at(300), zone: 'solar' }],
  ...over,
})

describe('state labels', () => {
  it('has a label and a tone for every state the API can send', () => {
    const all: DisplayState[] = ['seen', 'would_hold', 'held', 'runs_normally', 'no_window', 'skipped', 'owner_changed', 'left_alone', 'released', 'running', 'done', 'ended']
    for (const s of all) expect(STATE[s].label.length).toBeGreaterThan(2)
  })
})

describe('countdown', () => {
  it('counts down, then up', () => {
    expect(countdown(at(0), at(125))).toBe('in 2m 05s')
    expect(countdown(at(0), at(45))).toBe('in 45s')
    expect(countdown(at(0), at(0))).toBe('now')
    expect(countdown(at(0), at(-30))).toBe('30s ago')
    expect(countdown(at(0), at(-200))).toBe('3m 20s ago')
  })
})

describe('job line', () => {
  it('says what is happening in words, with the time of day', () => {
    expect(jobLine(job({ display: 'held', applied_start: at(120) }))).toBe('Held until 15:32:00')
    expect(jobLine(job({ display: 'would_hold', planned_start: at(120) }))).toBe('Would hold until 15:32:00')
    expect(jobLine(job({ display: 'running', actual_start: at(130) }))).toBe('Running since 15:32:10')
    expect(jobLine(job({ display: 'done', saved: 33.76 }))).toBe('Done · saved ₹33.76')
    expect(jobLine(job({ display: 'done', saved: null }))).toBe('Done')
    expect(jobLine(job({ display: 'skipped', note: 'array' }))).toBe('Left alone (array)')
    expect(jobLine(job({ display: 'owner_changed' }))).toBe('Changed by its owner: no longer managed')
  })
})

describe('agent status', () => {
  it('is connected when it reported recently, silent when not, and absent before the first report', () => {
    expect(agentStatus(view())).toEqual({ ok: true, label: 'Agent connected · 5 s ago' })
    expect(agentStatus(view({}, { last_seen_at: at(-200) }))).toEqual({ ok: false, label: 'Agent silent for 3m 20s' })
    expect(agentStatus(view({}, { last_seen_at: null }))).toEqual({ ok: false, label: 'No agent has connected yet' })
  })
})

describe('shift timeline layout', () => {
  it('puts the axis on the zone extent and positions every mark as a fraction of it', () => {
    const v = view({ jobs: [job({ ref: 'a', baseline_start: at(0), applied_start: at(150), planned_start: at(150) })] })
    const L = timelineLayout(v)
    expect(L.t0).toBe(T0 - 60_000) // zones run from -60 s to +300 s: a 360 s axis
    expect(L.nowX).toBeCloseTo(60 / 360)
    expect(L.zones.map((z) => [z.zone, +z.x0.toFixed(3), +z.x1.toFixed(3)])).toEqual([['peak', 0, 0.5], ['solar', 0.5, 1]])
    const r = L.rows[0]
    expect(r.fromX).toBeCloseTo(60 / 360)
    expect(r.toX).toBeCloseTo(210 / 360)
    expect(r.runX0).toBeNull() // no real run yet
  })

  it('draws a run bar once the job has really run, and follows a running job to now', () => {
    const done = timelineLayout(view({ jobs: [job({ baseline_start: at(0), actual_start: at(150), actual_end: at(210), display: 'done' })] })).rows[0]
    expect([done.runX0, done.runX1]).toEqual([expect.closeTo(210 / 360), expect.closeTo(270 / 360)])
    const running = timelineLayout(view({ jobs: [job({ baseline_start: at(0), actual_start: at(-30), display: 'running' })] })).rows[0]
    expect(running.runX1).toBeCloseTo(60 / 360) // up to now
  })

  it('keeps marks inside the axis, skips jobs with nothing to draw, and shows the newest first', () => {
    const L = timelineLayout(view({ jobs: [
      job({ ref: 'none' }),
      job({ ref: 'far', baseline_start: at(-9999), applied_start: at(9999), submit_time: at(-10) }),
      job({ ref: 'new', baseline_start: at(0), applied_start: at(150), submit_time: at(-1) }),
    ] }))
    expect(L.rows.map((r) => r.ref)).toEqual(['new', 'far'])
    expect(L.rows[1].fromX).toBe(0)
    expect(L.rows[1].toX).toBe(1)
  })

  it('still lays out when the tariff is unknown (no zones)', () => {
    const L = timelineLayout(view({ zones: [], jobs: [job({ baseline_start: at(0), applied_start: at(300) })] }))
    expect(L.zones).toEqual([])
    expect(L.t1 - L.t0).toBe(20 * 60_000) // 5 minutes back to 15 minutes ahead
  })
})
```

- [ ] **Step 4: Run and confirm it fails**

Run: `cd frontend; npx vitest run src/lib/live.test.ts`
Expected: FAIL, cannot resolve `./live`.

- [ ] **Step 5: Write `live.ts`**

Create `frontend/src/lib/live.ts`:

```ts
import type { DisplayState, SiteJob, SiteView, Zone } from '../types'
import { fmtInr } from './data'
import { fmtHMS } from './time'

export type Tone = 'hold' | 'run' | 'done' | 'muted' | 'warn'

/** One label and one tone per state the API can send. The tone picks the colour and a shape so colour is never alone. */
export const STATE: Record<DisplayState, { label: string; tone: Tone }> = {
  seen: { label: 'Waiting to be planned', tone: 'muted' },
  would_hold: { label: 'Would hold', tone: 'hold' },
  held: { label: 'Held', tone: 'hold' },
  runs_normally: { label: 'Runs as normal', tone: 'muted' },
  no_window: { label: 'No cheaper window', tone: 'muted' },
  skipped: { label: 'Left alone', tone: 'muted' },
  owner_changed: { label: 'Changed by owner', tone: 'warn' },
  left_alone: { label: 'Left alone', tone: 'warn' },
  released: { label: 'Released', tone: 'run' },
  running: { label: 'Running', tone: 'run' },
  done: { label: 'Done', tone: 'done' },
  ended: { label: 'Ended', tone: 'muted' },
}

const pad = (n: number) => String(n).padStart(2, '0')

/** "in 2m 05s", "45s ago", "now": seconds resolution, because the demo runs in minutes. */
export function countdown(nowIso: string, targetIso: string): string {
  const s = Math.round((new Date(targetIso).getTime() - new Date(nowIso).getTime()) / 1000)
  if (s === 0) return 'now'
  const a = Math.abs(s)
  const text = a >= 60 ? `${Math.floor(a / 60)}m ${pad(a % 60)}s` : `${a}s`
  return s > 0 ? `in ${text}` : `${text} ago`
}

/** What is happening to a job, in words. */
export function jobLine(j: SiteJob): string {
  switch (j.display) {
    case 'held': return `Held until ${fmtHMS(j.applied_start ?? j.planned_start!)}`
    case 'would_hold': return `Would hold until ${fmtHMS(j.planned_start!)}`
    case 'running': return `Running since ${fmtHMS(j.actual_start!)}`
    case 'done': return j.saved != null ? `Done · saved ${fmtInr(j.saved, 2)}` : 'Done'
    case 'skipped': return `Left alone${j.note ? ` (${j.note})` : ''}`
    case 'owner_changed': return 'Changed by its owner: no longer managed'
    case 'left_alone': return `Left alone${j.note ? ` (${j.note.replace(/^apply_failed: /, 'could not change it: ')})` : ''}`
    case 'released': return 'Released: starts as soon as Slurm can'
    case 'runs_normally': return 'Not worth moving: runs as Slurm decides'
    case 'no_window': return 'No cheaper window before its limit'
    case 'ended': return `Ended (${j.state.toLowerCase()})`
    default: return 'Waiting to be planned'
  }
}

const SILENT_AFTER_S = 90 // the agent reports every 10-30 s

export function agentStatus(v: SiteView): { ok: boolean; label: string } {
  if (!v.site.last_seen_at) return { ok: false, label: 'No agent has connected yet' }
  const age = Math.round((new Date(v.now).getTime() - new Date(v.site.last_seen_at).getTime()) / 1000)
  if (age <= SILENT_AFTER_S) return { ok: true, label: `Agent connected · ${age} s ago` }
  const a = countdown(v.now, v.site.last_seen_at) // "3m 20s ago": the report is in the past
  return { ok: false, label: `Agent silent for ${a.replace(' ago', '')}` }
}

export interface TimelineRow {
  ref: string
  display: DisplayState
  fromX: number | null // where it would have started
  toX: number | null // where it starts (set) or started (real)
  runX0: number | null // the real run, once there is one
  runX1: number | null
}

export interface TimelineLayout {
  t0: number
  t1: number
  nowX: number
  zones: { x0: number; x1: number; zone: Zone }[]
  rows: TimelineRow[]
}

const MAX_ROWS = 8

/** Everything the shift timeline draws, as fractions (0-1) of its time axis. The axis is the tariff zone extent the API sent. */
export function timelineLayout(v: SiteView): TimelineLayout {
  const now = new Date(v.now).getTime()
  const ms = (iso: string | null) => (iso ? new Date(iso).getTime() : null)
  const t0 = v.zones.length ? new Date(v.zones[0].start).getTime() : now - 5 * 60_000
  const t1 = v.zones.length ? new Date(v.zones[v.zones.length - 1].end).getTime() : now + 15 * 60_000
  const x = (t: number) => Math.min(1, Math.max(0, (t - t0) / (t1 - t0)))
  const opt = (t: number | null) => (t == null ? null : x(t))

  const rows: TimelineRow[] = v.jobs
    .filter((j) => j.baseline_start || j.applied_start || j.planned_start || j.actual_start)
    .sort((a, b) => (a.submit_time < b.submit_time ? 1 : a.submit_time > b.submit_time ? -1 : 0))
    .slice(0, MAX_ROWS)
    .map((j) => {
      const start = ms(j.actual_start)
      const to = start ?? ms(j.applied_start) ?? ms(j.planned_start)
      const end = ms(j.actual_end) ?? (j.display === 'running' ? now : null)
      return {
        ref: j.ref, display: j.display, fromX: opt(ms(j.baseline_start)), toX: opt(to),
        runX0: start != null && end != null ? x(start) : null, runX1: start != null && end != null ? x(end) : null,
      }
    })

  return {
    t0, t1, nowX: x(now), rows,
    zones: v.zones.map((z) => ({ x0: x(new Date(z.start).getTime()), x1: x(new Date(z.end).getTime()), zone: z.zone })),
  }
}
```

- [ ] **Step 6: Run the tests until they pass**

Run: `cd frontend; npx vitest run src/lib/live.test.ts`
Expected: all pass. If the `at(150)` positions are off by the `-60 s` axis start, recheck the expected values in the test against `t0 = zones[0].start`; the code is the reference for the axis, and the test's numbers are derived from `zones` in the `view()` helper.

- [ ] **Step 7: The polling hooks**

Create `frontend/src/useSitePoll.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from 'react'
import { getSites, getSiteView } from './api'
import type { SiteInfo, SiteView } from './types'

export interface Polled<T> {
  data: T | null
  error: string | null // set while the latest poll fails; `data` keeps the last good value
  refresh: () => void
}

function usePolled<T>(fetcher: (() => Promise<T>) | null, intervalMs: number): Polled<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null }>({ data: null, error: null })
  const tick = useRef<() => void>(() => {})
  useEffect(() => {
    if (!fetcher) return
    let alive = true
    const run = async () => {
      try {
        const data = await fetcher()
        if (alive) setState({ data, error: null })
      } catch (e) {
        if (alive) setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e) }))
      }
    }
    tick.current = run
    run()
    const id = setInterval(run, intervalMs)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [fetcher, intervalMs])
  const refresh = useCallback(() => tick.current(), [])
  return { ...state, refresh }
}

/** The sites this Wattshift knows. */
export const useSites = (intervalMs = 5000) => usePolled<SiteInfo[]>(getSites, intervalMs)

/** One site's whole page, every 2 seconds by default (the five-minute demo moves in seconds). */
export function useSitePoll(siteId: string | null, intervalMs = 2000): Polled<SiteView> {
  const fetcher = useCallback(() => getSiteView(siteId!), [siteId])
  return usePolled<SiteView>(siteId ? fetcher : null, intervalMs)
}
```

- [ ] **Step 8: Type-check and run the whole frontend suite**

Run: `cd frontend; npx tsc --noEmit; npx vitest run`
Expected: no type errors; all tests pass (31 existing plus the new ones).

---

### Task 4: The Live cluster page

**Files:**
- Create: `frontend/src/components/live/StateChip.tsx`, `LiveControls.tsx`, `LiveSummary.tsx`, `ShiftTimeline.tsx`, `LiveJobs.tsx`, `ActivityFeed.tsx`, `frontend/src/views/Live.tsx`, `backend/scripts/simulate_agent.py`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: everything from Task 3; existing `Card`, `DataTable`, `ZoneKey`, `Chip` (`components/Card.tsx`), `fmtInr`, `fmtHMS`, `ZONE_HEX`, `ZONE_LABEL`, the `wattshift.apiKey` sessionStorage key the Submit job dialog uses.
- Produces: the `#/live` tab. It polls one site every 2 seconds and shows: identity and operator controls (mode switch, kill switch, agent status); the measured saving with the plan shown separately; the **shift timeline**; a jobs table; an activity feed.

**Design notes (already decided):**
- The timeline is the signature visual: time runs left to right over the tariff zone bands (peak orange, normal violet, solar teal, from `ZONE_HEX`); each job is one row with a hollow circle where it **would have started**, an arrow to a filled circle where it **starts** (set or real), and a bar for the real run. A "now" line moves. It has a table twin (the Card `table` prop), as every chart in this app does.
- Meaning is never colour alone: each state has a glyph (`◔ ▶ ✓ ○ !`) and a word.
- Times show seconds (the demo moves in minutes); everything else uses the existing card styling and tokens.
- When the cloud cannot be reached the page keeps the last good data and says: "The cloud is not reachable. Jobs already held are still held: Slurm enforces their start times by itself." (This is the moment in the demo where the cloud is stopped.)

- [ ] **Step 1: The state chip and the controls**

Create `frontend/src/components/live/StateChip.tsx`:

```tsx
import type { DisplayState } from '../../types'
import { STATE, type Tone } from '../../lib/live'

export const TONE_COLOR: Record<Tone, string> = { hold: '#7f70d8', run: '#12a796', done: '#12a796', muted: '#8a81ab', warn: '#e65a3c' }
const GLYPH: Record<Tone, string> = { hold: '◔', run: '▶', done: '✓', muted: '○', warn: '!' }

export function StateChip({ state }: { state: DisplayState }) {
  const { label, tone } = STATE[state]
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold" style={{ color: TONE_COLOR[tone], boxShadow: `inset 0 0 0 1px ${TONE_COLOR[tone]}55` }}>
      <span aria-hidden>{GLYPH[tone]}</span>
      {label}
    </span>
  )
}
```

Create `frontend/src/components/live/LiveControls.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { releaseAll, setSiteMode, type OpResult } from '../../api'
import type { SiteView } from '../../types'
import { agentStatus } from '../../lib/live'
import { Chip } from '../Card'

const KEY_STORE = 'wattshift.apiKey' // shared with the Submit job dialog
const readKey = () => {
  try {
    return sessionStorage.getItem(KEY_STORE) ?? ''
  } catch {
    return ''
  }
}

export function LiveControls({ view, refresh }: { view: SiteView; refresh: () => void }) {
  const { site } = view
  const agent = agentStatus(view)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [needKey, setNeedKey] = useState(false)
  const [apiKey, setApiKey] = useState(readKey)
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    if (!confirming) return
    const t = setTimeout(() => setConfirming(false), 5000) // an unconfirmed kill switch disarms itself
    return () => clearTimeout(t)
  }, [confirming])

  async function run(op: (key?: string) => Promise<OpResult>) {
    setBusy(true)
    setMessage(null)
    const r = await op(apiKey || undefined)
    setBusy(false)
    if (r.kind === 'ok') {
      setNeedKey(false)
      try {
        if (apiKey) sessionStorage.setItem(KEY_STORE, apiKey)
      } catch { /* storage can be blocked; the key just is not remembered */ }
      refresh()
    } else if (r.kind === 'auth') {
      setNeedKey(true)
      setMessage(apiKey ? 'That API key was not accepted.' : 'This server needs an API key for operator actions.')
    } else setMessage(r.message)
  }

  const modeButton = (mode: 'shadow' | 'autonomous', label: string, hint: string) => (
    <button
      type="button" disabled={busy} aria-pressed={site.mode === mode} title={hint}
      onClick={() => site.mode !== mode && run((k) => setSiteMode(site.id, mode, k))}
      className="rounded-full px-4 py-1.5 text-sm font-semibold text-ink-2 hover:text-ink aria-pressed:bg-normal aria-pressed:text-white disabled:opacity-60"
    >
      {label}
    </button>
  )

  return (
    <div className="col-span-12 flex flex-col gap-3">
      <div className="card flex flex-wrap items-center justify-between gap-x-6 gap-y-3 px-5 py-4">
        <div className="min-w-0">
          <h2 className="font-display text-xl font-bold tracking-tight text-ink">{site.company} · {site.name}</h2>
          <p className="eyebrow mt-0.5">{site.gpus} GPUs · {site.tariff.replace('TEST E2E', 'demo tariff')}</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Chip tone={agent.ok ? 'plain' : 'note'}>
            <span aria-hidden className={`size-2 rounded-full ${agent.ok ? 'bg-solar' : 'bg-peak'}`} />
            {agent.label}
          </Chip>
          <div role="group" aria-label="Mode" className="flex gap-1 rounded-full bg-card p-1 shadow-[inset_0_0_0_1px_var(--color-hair)]">
            {modeButton('shadow', 'Shadow', 'Watch and report only: nothing in Slurm is changed')}
            {modeButton('autonomous', 'Autonomous', 'Set start times in Slurm')}
          </div>
          <button
            type="button" disabled={busy || site.release_all}
            onClick={() => (confirming ? (setConfirming(false), run((k) => releaseAll(site.id, true, k))) : setConfirming(true))}
            className={`rounded-full px-4 py-1.5 text-sm font-semibold shadow-[inset_0_0_0_1px_var(--color-peak)] disabled:opacity-50 ${confirming ? 'bg-peak text-white' : 'text-peak hover:bg-card-hi'}`}
          >
            {confirming ? 'Click again to release everything' : 'Release all held jobs'}
          </button>
        </div>
      </div>

      {site.release_all && (
        <p role="status" className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-card-hi px-4 py-2.5 text-sm text-ink shadow-[inset_0_0_0_1px_var(--color-peak)]">
          <span>Kill switch is on: every held job was set back to start now, and planning is paused for this site.</span>
          <button type="button" disabled={busy} onClick={() => run((k) => releaseAll(site.id, false, k))} className="rounded-full px-3 py-1 text-xs font-semibold text-ink shadow-[inset_0_0_0_1px_var(--color-hair-2)] hover:bg-card">
            Turn off
          </button>
        </p>
      )}

      {(message || needKey) && (
        <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl bg-card-hi px-4 py-2.5 text-sm text-ink shadow-[inset_0_0_0_1px_var(--color-hair-2)]">
          {message && <span>{message}</span>}
          {needKey && (
            <label className="flex items-center gap-2 text-xs text-ink-2">
              API key
              <input type="password" autoComplete="off" spellCheck={false} value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="rounded-lg bg-card px-2 py-1 text-ink shadow-[inset_0_0_0_1px_var(--color-hair-2)]" />
            </label>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: The summary, the jobs table and the activity feed**

Create `frontend/src/components/live/LiveSummary.tsx`:

```tsx
import type { SiteView } from '../../types'
import { fmtInr } from '../../lib/data'
import { Card } from '../Card'

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div title={hint}>
      <div className="eyebrow">{label}</div>
      <div className="num-tab mt-0.5 text-lg font-semibold text-ink">{value}</div>
    </div>
  )
}

export function LiveSummary({ view }: { view: SiteView }) {
  const s = view.summary
  const n = (k: keyof typeof s.counts) => s.counts[k] ?? 0
  const measured = s.jobs_measured > 0
  return (
    <Card title="₹ saved" subtitle="Measured from the real start and end times Slurm reported" className="col-span-12 md:col-span-5 lg:col-span-4">
      <div className="text-[56px] font-semibold leading-none tracking-tight text-ink num-tab">{fmtInr(s.saved, 2)}</div>
      <p className="mt-2 text-sm text-ink-2">
        {measured
          ? `${s.pct_saved ?? 0}% lower than starting when Slurm would have, across ${s.jobs_measured} finished job${s.jobs_measured === 1 ? '' : 's'}`
          : 'Nothing measured yet. A saving appears once a held job has really run.'}
      </p>
      <div className="mt-4 grid grid-cols-2 gap-3 border-t border-hair pt-3">
        <Stat label="Planned, not run yet" value={fmtInr(s.potential, 2)} hint="An estimate from the plan. It is never added to the measured figure." />
        <Stat label="Held" value={String(n('held') + n('would_hold'))} />
        <Stat label="Running" value={String(n('running'))} />
        <Stat label="Done" value={String(n('done'))} />
      </div>
      <p className="eyebrow mt-3">Modeled power: GPUs × kW per GPU × the tariff. Not a measured bill.</p>
    </Card>
  )
}
```

Create `frontend/src/components/live/LiveJobs.tsx`:

```tsx
import type { SiteJob } from '../../types'
import { fmtInr } from '../../lib/data'
import { jobLine } from '../../lib/live'
import { fmtHMS } from '../../lib/time'
import { Card, DataTable } from '../Card'
import { StateChip } from './StateChip'

const t = (iso: string | null) => (iso ? fmtHMS(iso) : '—')

export function LiveJobs({ jobs }: { jobs: SiteJob[] }) {
  return (
    <Card title="Jobs" subtitle={jobs.length ? `${jobs.length} reported by the agent` : undefined} className="col-span-12 lg:col-span-8">
      <DataTable
        head={['Job', 'State', 'What is happening', 'GPUs', 'Would have started', 'Starts / started', 'Cost if not moved → actual']}
        empty="No jobs yet. Jobs that match your flex rules appear here within a few seconds of being submitted."
        rows={jobs.map((j) => [
          <span translate="no">{j.ref}</span>,
          <StateChip state={j.display} />,
          jobLine(j),
          j.gpus ?? '—',
          t(j.baseline_start),
          t(j.actual_start ?? j.applied_start ?? j.planned_start),
          j.baseline_cost != null ? `${fmtInr(j.baseline_cost, 2)} → ${j.actual_cost != null ? fmtInr(j.actual_cost, 2) : j.planned_cost != null ? `${fmtInr(j.planned_cost, 2)} (plan)` : '—'}` : '—',
        ])}
      />
    </Card>
  )
}
```

Create `frontend/src/components/live/ActivityFeed.tsx`:

```tsx
import type { SiteView } from '../../types'
import { fmtHMS } from '../../lib/time'
import { Card } from '../Card'

export function ActivityFeed({ activity }: { activity: SiteView['activity'] }) {
  return (
    <Card title="What just happened" subtitle="Newest first" className="col-span-12">
      {activity.length === 0 ? (
        <p className="py-4 text-sm text-ink-2">Nothing yet.</p>
      ) : (
        <ol className="max-h-64 space-y-1.5 overflow-auto text-sm">
          {activity.map((a, i) => (
            <li key={`${a.at}-${i}`} className="flex gap-3">
              <time dateTime={a.at} className="num-tab w-[68px] shrink-0 text-xs text-ink-3">{fmtHMS(a.at)}</time>
              <span className="text-ink-2">{a.text}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}
```

- [ ] **Step 3: The shift timeline**

Create `frontend/src/components/live/ShiftTimeline.tsx`:

```tsx
import type { SiteView } from '../../types'
import { fmtHMS } from '../../lib/time'
import { timelineLayout } from '../../lib/live'
import { ZONE_HEX } from '../../lib/zones'
import { Card, DataTable, ZoneKey } from '../Card'
import { TONE_COLOR } from './StateChip'
import { STATE } from '../../lib/live'

const W = 1000, GUTTER = 92, PLOT = W - GUTTER - 8, ROW = 34, TOP = 8, AXIS = 26

export function ShiftTimeline({ view }: { view: SiteView }) {
  const L = timelineLayout(view)
  const H = TOP + Math.max(L.rows.length, 1) * ROW + AXIS
  const X = (f: number) => GUTTER + f * PLOT
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({ f, label: fmtHMS(new Date(L.t0 + f * (L.t1 - L.t0)).toISOString()) }))
  const time = (f: number | null) => (f == null ? '—' : fmtHMS(new Date(L.t0 + f * (L.t1 - L.t0)).toISOString()))

  const table = (
    <DataTable
      head={['Job', 'Would have started', 'Starts / started', 'Ran until']}
      rows={L.rows.map((r) => [r.ref, time(r.fromX), time(r.toX), time(r.runX1)])}
      empty="No job has a start time yet."
    />
  )

  return (
    <Card title="Shift timeline" subtitle="Where each job would have started, and where it starts instead" table={table} className="col-span-12">
      <ZoneKey />
      <div role="img" aria-label={`Timeline of ${L.rows.length} jobs across the tariff zones. A table view is available.`} className="mt-2">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 420 }}>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 1 10 5 0 9z" fill="context-stroke" />
            </marker>
          </defs>
          {L.zones.map((z, i) => (
            <rect key={i} x={X(z.x0)} y={TOP} width={Math.max(0, X(z.x1) - X(z.x0))} height={H - TOP - AXIS} fill={ZONE_HEX[z.zone]} opacity={0.16} />
          ))}
          {ticks.map((t) => (
            <g key={t.f}>
              <line x1={X(t.f)} x2={X(t.f)} y1={TOP} y2={H - AXIS} stroke="rgb(255 255 255 / 0.07)" />
              <text x={X(t.f)} y={H - 8} textAnchor={t.f === 0 ? 'start' : t.f === 1 ? 'end' : 'middle'} fontSize="12" fill="#8a81ab" className="num-tab">{t.label}</text>
            </g>
          ))}
          {L.rows.map((r, i) => {
            const y = TOP + i * ROW + ROW / 2
            const color = TONE_COLOR[STATE[r.display].tone]
            return (
              <g key={r.ref}>
                <text x={GUTTER - 10} y={y + 4} textAnchor="end" fontSize="12" fill="#b7aed0">job {r.ref}</text>
                {r.fromX != null && r.toX != null && Math.abs(r.toX - r.fromX) > 0.004 && (
                  <line x1={X(r.fromX)} x2={X(r.toX) - 7} y1={y} y2={y} stroke={color} strokeWidth="2" strokeDasharray="5 4" markerEnd="url(#arrow)" />
                )}
                {r.runX0 != null && r.runX1 != null && <rect x={X(r.runX0)} y={y - 7} width={Math.max(4, X(r.runX1) - X(r.runX0))} height="14" rx="4" fill={color} />}
                {r.fromX != null && <circle cx={X(r.fromX)} cy={y} r="6" fill="#14101f" stroke={color} strokeWidth="2" />}
                {r.toX != null && r.runX0 == null && <circle cx={X(r.toX)} cy={y} r="6" fill={color} />}
              </g>
            )
          })}
          <line x1={X(L.nowX)} x2={X(L.nowX)} y1={TOP} y2={H - AXIS} stroke="#f3f0fa" strokeWidth="1.5" />
          <text x={X(L.nowX) + 6} y={TOP + 12} fontSize="12" fontWeight="700" fill="#f3f0fa">now</text>
        </svg>
      </div>
      <p className="eyebrow mt-2">○ where it would have started · ● where it starts · ▬ the real run</p>
    </Card>
  )
}
```

- [ ] **Step 4: The view and the tab**

Create `frontend/src/views/Live.tsx`:

```tsx
import { useState } from 'react'
import { useSitePoll, useSites } from '../useSitePoll'
import { ActivityFeed } from '../components/live/ActivityFeed'
import { LiveControls } from '../components/live/LiveControls'
import { LiveJobs } from '../components/live/LiveJobs'
import { LiveSummary } from '../components/live/LiveSummary'
import { ShiftTimeline } from '../components/live/ShiftTimeline'

export function Live() {
  const sites = useSites()
  const [picked, setPicked] = useState<string | null>(null)
  const siteId = picked ?? sites.data?.[0]?.id ?? null
  const poll = useSitePoll(siteId)

  if (sites.data && sites.data.length === 0) {
    return (
      <p className="card p-8 text-ink-2">
        No customer site is connected yet. Create one with <code>python -m scripts.onboard_site</code> and start the agent; its jobs will appear here.
      </p>
    )
  }
  if (!poll.data) {
    return <p className="card p-8 text-ink-2">{poll.error || sites.error ? 'Waiting for the cloud to come up…' : 'Loading…'}</p>
  }
  const v = poll.data
  return (
    <div className="grid grid-cols-12 gap-4">
      {poll.error && (
        <p role="status" className="col-span-12 rounded-xl bg-card-hi px-4 py-2.5 text-sm text-ink shadow-[inset_0_0_0_1px_var(--color-peak)]">
          The cloud is not reachable. Jobs already held are still held: Slurm enforces their start times by itself. Retrying every 2 seconds.
        </p>
      )}
      {sites.data && sites.data.length > 1 && (
        <label className="col-span-12 flex items-center gap-2 text-sm text-ink-2">
          Site
          <select value={siteId ?? ''} onChange={(e) => setPicked(e.target.value)} className="rounded-lg bg-card px-2 py-1 text-ink shadow-[inset_0_0_0_1px_var(--color-hair-2)]">
            {sites.data.map((s) => <option key={s.id} value={s.id}>{s.company} · {s.name}</option>)}
          </select>
        </label>
      )}
      <LiveControls view={v} refresh={poll.refresh} />
      <LiveSummary view={v} />
      <LiveJobs jobs={v.jobs} />
      <ShiftTimeline view={v} />
      <ActivityFeed activity={v.activity} />
    </div>
  )
}
```

In `frontend/src/App.tsx`: add `import { Live } from './views/Live'`; add `{ id: 'live', label: 'Live cluster' },` to `TABS` directly after the `dashboard` entry; render the Live view before the `!d` gate and hide the global "Can't reach" banner on that tab. Concretely, change `{poll.error && (` to `{poll.error && tab !== 'live' && (`, and change

```tsx
        {tab === 'backtest' ? (
          <Backtest />
        ) : !d ? (
```

to

```tsx
        {tab === 'backtest' ? (
          <Backtest />
        ) : tab === 'live' ? (
          <Live />
        ) : !d ? (
```

(The Live tab must not depend on the old snapshot: the time-lapse demo's cloud has no live price feed for `/forecast`.)

- [ ] **Step 5: Type-check, build and test**

Run: `cd frontend; npx tsc --noEmit; npx vitest run; npm run build`
Expected: no errors; all tests pass; the production build succeeds. Fix type errors in the component code (for example the `keyof typeof s.counts` index in `LiveSummary`: if TypeScript objects, type the helper as `(k: DisplayState)` and import the type).

- [ ] **Step 6: A simulated agent, to see the page with real data and no Docker**

Create `backend/scripts/simulate_agent.py`:

```python
"""Feed one site a scripted sequence of agent reports (no Slurm needed): to look at the Live page, and as a demo fallback.
The site must be in autonomous mode.  Usage:
  python -m scripts.simulate_agent --url http://localhost:8000 --key wsk_... [--pace 4]
Steps: three flex jobs appear; the cloud decides start times; the agent confirms them; one is changed by its owner; two start;
two finish (the cloud then measures their saving from the reported real times)."""
import argparse
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone

FMT = "%Y-%m-%dT%H:%M:%SZ"


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime(FMT)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--key", required=True)
    p.add_argument("--pace", type=float, default=4.0, help="seconds between steps")
    a = p.parse_args()

    def sync(jobs, applied=()):
        body = {"agent_version": "0.1.0", "mode": "autonomous", "sent_at": iso(datetime.now(timezone.utc)), "jobs": jobs, "applied": list(applied)}
        req = urllib.request.Request(a.url + "/agent/v1/sync", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Site-Key": a.key})
        return json.loads(urllib.request.urlopen(req, timeout=20).read())

    now = datetime.now(timezone.utc)
    base = {"state": "PENDING", "submit_time": iso(now), "time_limit_min": 60, "max_wait_min": 1440}
    jobs = {"9101": {**base, "ref": "9101", "gpus": 8}, "9102": {**base, "ref": "9102", "gpus": 4}, "9103": {**base, "ref": "9103", "gpus": 2}}
    print("1. three flex jobs appear")
    out = sync(list(jobs.values()))
    starts = {d["ref"]: datetime.strptime(d["start_at"], FMT).replace(tzinfo=timezone.utc) for d in out["decisions"]}
    print("   the cloud decided:", {r: iso(t) for r, t in starts.items()} or "nothing (no cheaper window)")
    time.sleep(a.pace)
    print("2. the agent confirms the start times")
    sync(list(jobs.values()), [{"ref": r, "start_at": iso(t), "ok": True} for r, t in starts.items()])
    time.sleep(a.pace)
    print("3. the owner of job 9103 changes its start time")
    jobs["9103"]["override"] = True
    sync(list(jobs.values()))
    time.sleep(a.pace)
    print("4. jobs 9101 and 9102 start at their set times")
    for r in ("9101", "9102"):
        if r in starts:
            jobs[r].update(state="RUNNING", start_time=iso(starts[r]))
    sync(list(jobs.values()))
    time.sleep(a.pace)
    print("5. they finish; the cloud measures the saving")
    for r in ("9101", "9102"):
        if r in starts:
            jobs[r].update(state="COMPLETED", end_time=iso(starts[r] + timedelta(minutes=60)))
    sync(list(jobs.values()))
    print("done: open the Live cluster page")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Look at it in the browser**

With the dev API and Vite server running (start them as in earlier phases if they are not), create a site, switch it to autonomous, run the simulation, and open the page in the built-in browser:

```powershell
cd C:\Users\aakur\OneDrive\Desktop\WattShift\backend
$env:PYTHONPATH=(Get-Location).Path
.\.venv\Scripts\python -m scripts.init_db
.\.venv\Scripts\python -m scripts.onboard_site --company Acme --site Pune-1 --gpus 64 --mode autonomous   # note the key it prints
.\.venv\Scripts\python -m scripts.simulate_agent --key <the key> --pace 6
```

Then open `http://localhost:5173/#/live`. Verify with screenshots at desktop width and at 375 px: the header shows the site, agent status, mode switch and kill switch; the timeline shows rows with hollow circles, dashed arrows and filled circles (and run bars for the two finished jobs) over zone bands; the summary shows a measured ₹ figure only after step 5 and a separate "planned" figure before it; job states carry glyphs and words; the console has no errors; nothing scrolls horizontally on the phone width; the Table switch on the timeline works. Then stop the API and reload: the "cloud is not reachable" message appears on the Live tab and the old data stays. Fix what looks wrong, and save two screenshots for the backup pack (Task 6).

---

### Task 5: The time-lapse tariff and the demo cloud

**Files:**
- Create: `e2e/timelapse.py`, `e2e/demo_cloud_main.py`, `e2e/test_timelapse.py`
- Modify: `e2e/cloud.py` (from Task 3 of the end-to-end plan: `seed` gains two parameters, `start` gains one, and a new `set_peak`)

**Interfaces:**
- Produces (in `timelapse.py`): `PERIOD_S = 120`, `MIN_LEAD_S = 230`, `pseudo_hour(ts) -> int`, `choose_boundary(now) -> datetime`, `peak_pseudo_hours(now, boundary) -> set[int]`, `tod_zone(ts, rules)`, `apply()` (the in-process patches).
- Produces (in `cloud.py`): `seed(now, peak, step=STEP, hours=76)`, `start(launcher="cloud_main.py")`, `set_peak(peak: set[int])` (rewrites the demo tariff's peak hours without re-seeding, so the demo can start whenever the presenter is ready).

**What the patches do (all in the demo cloud's process only; production code is unchanged):** every `PERIOD_S` = 120 seconds counts as one tariff "hour" (`pseudo_hour = floor(epoch / 120) mod 24`), so the cheap window is at most a few minutes away; the allocator's block is 1 minute instead of 15 (`allocator.BLOCK_MIN`, `allocator.BLOCK`, `planner.BLOCK_MIN`); the start-time spread is 0; the agent's poll interval is 10 seconds (`agent_sync.POLL_SECONDS`). The real tariff code, allocator, planner, measurement and sync run unchanged on top of these.

- [ ] **Step 1: Write the failing tests**

Create `e2e/test_timelapse.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import cloud
import timelapse


def at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


@pytest.fixture()
def restore(monkeypatch):
    """apply() changes module attributes; register the originals so every test undoes it."""
    from app import agent_sync, allocator, planner, tariff

    for mod, name in [(allocator, "BLOCK_MIN"), (allocator, "BLOCK"), (allocator, "jitter_minutes"), (planner, "BLOCK_MIN"),
                      (tariff, "tod_zone"), (agent_sync, "POLL_SECONDS")]:
        monkeypatch.setattr(mod, name, getattr(mod, name))


def test_a_period_is_one_pseudo_hour_and_it_wraps_at_24():
    assert timelapse.pseudo_hour(at(120 * 1000)) == 1000 % 24
    assert timelapse.pseudo_hour(at(120 * 1000 + 119)) == 1000 % 24
    assert timelapse.pseudo_hour(at(120 * 1001)) == 1001 % 24
    assert {timelapse.pseudo_hour(at(120 * i)) for i in range(48)} == set(range(24))


def test_the_boundary_is_a_period_edge_with_enough_lead():
    base = 120 * 1000
    assert timelapse.choose_boundary(at(base + 10)) == at(base + 240)  # the next edge is only 110 s away: too tight, take the one after
    assert timelapse.choose_boundary(at(base + 100)) == at(base + 240)  # the next edge is 20 s away; the one after is 140 s: enough
    assert timelapse.choose_boundary(at(base)) == at(base + 240)  # exactly on an edge: the next is 120 s away, too tight


def test_the_peak_covers_the_pseudo_hours_from_now_to_the_boundary():
    now = at(120 * 1000 + 10)
    b = timelapse.choose_boundary(now)
    assert timelapse.peak_pseudo_hours(now, b) == {1000 % 24, 1001 % 24}


def test_the_patched_zone_lookup_uses_pseudo_hours(restore):
    from app.catalogue import _rules_from_json

    rules = _rules_from_json(cloud.hour_rules({1000 % 24, 1001 % 24}))
    assert timelapse.tod_zone(at(120 * 1000 + 5), rules).zone == "peak"
    assert timelapse.tod_zone(at(120 * 1002 + 5), rules).zone == "solar"
    with pytest.raises(ValueError):
        timelapse.tod_zone(datetime(2026, 9, 21, 10, 0), rules)  # naive time


def test_apply_switches_the_allocator_to_one_minute_blocks_without_a_spread(restore):
    from app import agent_sync, allocator, planner, tariff

    timelapse.apply()
    t = datetime(2026, 9, 21, 10, 34, 56, tzinfo=timezone.utc)
    assert allocator.floor_block(t) == t.replace(second=0)  # a minute, not a quarter hour
    assert allocator.overlaps(t.replace(second=0), 3) == [(t.replace(second=0) + timedelta(minutes=i), 1) for i in range(3)]
    assert all(allocator.jitter_minutes(f"job-{i}") == 0 for i in range(30))  # 1-minute blocks make the per-job spread (hash % BLOCK_MIN) zero; with 15-minute blocks these would mostly not be 0
    assert planner.BLOCK_MIN == 1 and agent_sync.POLL_SECONDS == 10
    assert tariff.tod_zone is timelapse.tod_zone


def test_the_real_planner_defers_a_job_to_the_time_lapse_boundary(restore):
    """The real sync + planner + tariff code, on the time-lapse tariff, against the test database."""
    from sqlalchemy.orm import Session

    from app import agent_sync, db
    from app.models import Site

    timelapse.apply()
    now = at(120 * 1000 + 10)
    boundary = timelapse.choose_boundary(now)
    cloud.seed(now, timelapse.peak_pseudo_hours(now, boundary), step=timedelta(minutes=1), hours=3)
    job = {"ref": "1", "state": "PENDING", "submit_time": now.isoformat(), "gpus": 2, "time_limit_min": 1, "max_wait_min": 180}
    body = agent_sync.SyncIn.model_validate({"agent_version": "0", "mode": "autonomous", "sent_at": now.isoformat(), "jobs": [job]})
    with Session(db.make_engine(cloud.db_url())) as s, s.begin():
        site = s.get(Site, uuid.UUID(cloud.site_id()))
        site.mode = "autonomous"
        s.flush()
        out = agent_sync.process_sync(s, site, body, now)
    assert [d.start_at for d in out.decisions] == [boundary]  # exactly the moment the time-lapse tariff turns cheap
    assert out.next_poll_s == 10
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `.\backend\.venv\Scripts\python -m pytest e2e/test_timelapse.py -q -p no:cacheprovider`
Expected: FAIL, `ModuleNotFoundError: No module named 'timelapse'`.

- [ ] **Step 3: Write `timelapse.py`**

Create `e2e/timelapse.py`:

```python
"""The time-lapse tariff for the five-minute demo. Applied in the demo cloud's process ONLY (see demo_cloud_main.py):
every PERIOD_S seconds counts as one tariff "hour", so the cheap window is minutes away instead of up to an hour. The real
tariff, planner, measurement and sync code run unchanged on top of these patches, and the demo says it is a time-lapse."""
from datetime import datetime, timedelta

PERIOD_S = 120  # one pseudo-hour
MIN_LEAD_S = 230  # before the cheap window opens: the cloud will not set a start time closer than its 120 s start margin


def pseudo_hour(ts: datetime) -> int:
    return int(ts.timestamp() // PERIOD_S) % 24


def choose_boundary(now: datetime, min_lead_s: int = MIN_LEAD_S) -> datetime:
    """The next period edge that is at least min_lead_s away: the moment the demo tariff turns cheap."""
    edge = (int(now.timestamp()) // PERIOD_S + 1) * PERIOD_S
    while edge - now.timestamp() < min_lead_s:
        edge += PERIOD_S
    return datetime.fromtimestamp(edge, now.tzinfo)


def peak_pseudo_hours(now: datetime, boundary: datetime) -> set[int]:
    """The pseudo-hours from now up to the boundary are expensive; every other one is cheap."""
    first = int(now.timestamp()) // PERIOD_S
    last = int(boundary.timestamp()) // PERIOD_S  # exclusive
    return {h % 24 for h in range(first, last)}


def tod_zone(ts: datetime, rules):
    """Replaces app.tariff.tod_zone: the zone is looked up by pseudo-hour instead of the IST clock hour."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime; pass a tz-aware timestamp")
    h = pseudo_hour(ts)
    for r in rules:
        if r.start_hour <= h < r.end_hour:
            return r
    raise LookupError(f"no ToD rule covers pseudo-hour {h}")


def apply() -> None:
    from app import agent_sync, allocator, planner, tariff

    allocator.BLOCK_MIN = 1  # 1-minute blocks (floor_block and overlaps read these at call time)
    allocator.BLOCK = timedelta(minutes=1)
    planner.BLOCK_MIN = 1  # planner imported the number by value, so it needs its own patch
    # (the per-job start-time spread is `hash % BLOCK_MIN`, so with 1-minute blocks it is 0 without a separate patch)
    tariff.tod_zone = tod_zone  # tod_multiplier and the dashboard's zone bands call it through the module
    agent_sync.POLL_SECONDS = 10  # the agent looks every 10 s in the demo
```

Create `e2e/demo_cloud_main.py`:

```python
"""Start the real cloud for the demo with the time-lapse tariff applied in this process only.  python demo_cloud_main.py <port>"""
import sys

import uvicorn

import timelapse

timelapse.apply()

if __name__ == "__main__":
    uvicorn.run("app.main:app", port=int(sys.argv[1]))
```

- [ ] **Step 4: Extend `cloud.py`**

In `e2e/cloud.py` change the `seed` signature and its price loop, `start`, and add `set_peak`:

```python
def seed(now: datetime, peak: set[int], step: timedelta = STEP, hours: int = 76) -> str:
```

Replace the two lines that set `t` and `end` and the loop that adds `PriceSignal` rows with:

```python
        if step >= timedelta(minutes=15):
            t = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        else:
            t = now.astimezone(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=10)  # the time-lapse demo
        end = t + timedelta(hours=hours)
        while t < end:
            s.add(PriceSignal(ts=t, price_rs_per_mwh=PRICE, source="iex_dam"))
            t += step
```

Change `def start() -> subprocess.Popen:` to `def start(launcher: str = "cloud_main.py") -> subprocess.Popen:` and its `Popen` argument `str(HERE / "cloud_main.py")` to `str(HERE / launcher)`. Append:

```python
def set_peak(peak: set[int]) -> None:
    """Rewrite the E2E tariff's expensive hours in place (the demo re-aims the cheap window when the presenter starts)."""
    from app.models import TariffCatalogue

    with session() as s:
        row = s.scalar(select(TariffCatalogue).where(TariffCatalogue.utility == "TEST", TariffCatalogue.category == "E2E"))
        row.rules = hour_rules(peak)
        s.commit()
```

- [ ] **Step 5: Run the tests**

Run: `.\backend\.venv\Scripts\python -m pytest e2e/ -q -p no:cacheprovider`
Expected: all pass (the earlier helper tests too). `test_the_real_planner_defers_a_job_to_the_time_lapse_boundary` needs `TEST_DATABASE_URL`; it recreates the test tables (like the backend tests do). If it fails with the decision at a later time than `boundary`, the price rows or the allocator patch are not being applied: check that `apply()` ran before `seed` and that `restore` did not undo it (the fixture registers originals but does not revert until teardown).

- [ ] **Step 6: Mutation-check**

In `timelapse.apply()` comment out `planner.BLOCK_MIN = 1` and confirm the planner test or the apply test fails (the planner would compute a 15-minute cap); comment out `tariff.tod_zone = tod_zone` and confirm `test_the_real_planner_defers...` fails (the cloud would see the real clock's zones). Restore both.

---

### Task 6: The five-minute demo script, the rehearsal and the backup pack

**Files:**
- Create: `e2e/demo.py`, `docs/demo/storyboard.md`, `docs/demo/screens/*.png` (from the rehearsal), `docs/demo/transcript.txt` (written by the script)

**Interfaces:**
- Consumes: the lab and process helpers (`lab`, `cloud`, `agentproc`, Task 0), the time-lapse cloud (Task 5), the Live page (Task 4), the measurement (Task 1).
- Produces: `python e2e/demo.py [--auto] [--dry-run] [--no-vite]`. It prepares everything (not part of the five minutes), waits for the presenter, then runs seven narrated steps against the real Slurm, agent and cloud: waiting jobs; shadow mode; the presenter (or the script) switches to autonomous; Slurm's own view of the held jobs; the agent and the cloud are stopped; the jobs start on time by themselves; the cloud comes back and shows the measured saving. It writes everything printed to `docs/demo/transcript.txt`.

**The story on screen (about 5 minutes):**

| Time | Narration | The dashboard shows | The terminal shows |
|---|---|---|---|
| 0:00 | "Electricity is expensive right now and turns cheap at HH:MM:SS. (A time-lapse: two minutes stand for one tariff hour.) Three GPU jobs are waiting behind a busy cluster." | nothing yet, then the jobs appear | `squeue`: three pending jobs |
| 0:25 | "Shadow mode. Wattshift only watches and plans. Nothing in Slurm changes." | "Would hold" chips, a planned ₹ figure | `scontrol`: no start time set |
| 0:50 | "Now I switch the site to autonomous." (click) | mode switch, the jobs turn "Held" | `scontrol`: start time = the cheap moment |
| 1:20 | "The GPUs are free, and Slurm still holds the jobs, because Wattshift set their start time." | timeline: jobs slid right into the cheap zone | `sinfo`: idle; `squeue`: pending, BeginTime |
| 1:40 | "Now I stop the agent and the cloud completely." | "The cloud is not reachable... Slurm enforces the start times by itself" | `squeue`: still held |
| 1:50 to ~3:30 | countdown | jobs still held | seconds remaining |
| ~3:30 | "The jobs just started, with nothing of ours running." | (banner still up) | `squeue`: running |
| ~4:15 | "I bring the cloud back. It learns what really happened from Slurm's records." | done jobs, measured ₹ saved, run bars on the timeline | the total saved |

- [ ] **Step 1: Write the script**

Create `e2e/demo.py`:

```python
"""The five-minute demo. Real Slurm (Docker), the real agent, the real cloud logic; only the tariff clock is compressed
(a time-lapse: every 2 minutes count as one tariff hour). Run `python e2e/demo.py`, open the printed URL, press Enter.
--auto runs without waiting for the presenter; --dry-run prints the storyboard; --no-vite leaves the dashboard server alone."""
import argparse
import os
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "backend")]
import agentproc  # noqa: E402
import cloud  # noqa: E402
import lab  # noqa: E402
import timelapse  # noqa: E402

DEMO = ROOT / "docs" / "demo"
FRONT_PORT = 5174  # the dev dashboard (5173) keeps talking to the normal API
URL = f"http://localhost:{FRONT_PORT}/#/live"
BLOCK_S = 75  # the busy cluster: two 4-GPU jobs that hold every GPU for this long
IST_ENV = {"TZ": "IST-5:30"}  # show Slurm's own times in IST, like the dashboard
LINES: list[str] = []


def say(text="", big=False):
    line = f"\n>>> {text}" if big else text
    print(line, flush=True)
    LINES.append(line)


def show(title, argv):
    """Run a Slurm command as the Operator and print it like a terminal would."""
    out = lab.dexec(argv, user="wsagent", env=IST_ENV, check=False).rstrip()
    say(f"$ {title}\n{out}")


def scontrol_lines(job, note=""):
    d = {}
    for tok in lab.dexec(["scontrol", "show", "job", str(job)], user="wsagent", env=IST_ENV).split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            d.setdefault(k, v)
    say(f"$ scontrol show job {job}   (only the interesting lines)\n  JobState={d.get('JobState')}  Reason={d.get('Reason')}  EligibleTime={d.get('EligibleTime')}{note}")


def wait_http(url, seconds=60):
    for _ in range(seconds):
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except OSError:
            time.sleep(1)
    return False


def start_vite():
    if wait_http(f"http://localhost:{FRONT_PORT}", 1):
        return None
    log = open(cloud.RUN / "vite-demo.log", "ab")
    env = {**os.environ, "VITE_API_TARGET": cloud.BASE}
    p = subprocess.Popen(["npx.cmd", "vite", "--port", str(FRONT_PORT), "--strictPort"], cwd=ROOT / "frontend", env=env, stdout=log, stderr=log)
    if not wait_http(f"http://localhost:{FRONT_PORT}", 60):
        p.kill()
        raise RuntimeError("the dashboard server did not start; see e2e/.run/vite-demo.log")
    return p


def capture(name):
    edge = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if Path(p).exists()), None)
    if edge is None:
        return
    (DEMO / "screens").mkdir(parents=True, exist_ok=True)
    subprocess.run([edge, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1440,1300", "--virtual-time-budget=8000",
                    f"--screenshot={DEMO / 'screens' / (name + '.png')}", URL], capture_output=True, timeout=90)
    say(f"(saved screenshot {name}.png)")


def storyboard():
    say("Storyboard (about 5 minutes): waiting jobs 0:00 | shadow mode 0:25 | switch to autonomous 0:50 | Slurm holds the jobs 1:20 | "
        "stop the agent and the cloud 1:40 | the jobs start by themselves ~3:30 | the cloud returns with the measured saving ~4:15")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-vite", action="store_true")
    ap.add_argument("--capture", action="store_true", help="save dashboard screenshots to docs/demo/screens (slower: the demo waits for each)")
    args = ap.parse_args()
    if args.dry_run:
        storyboard()
        return 0
    procs: dict[str, object] = {}
    try:
        # ---- preparation (not part of the five minutes) ----
        say("Preparing: the lab, the demo cloud and the dashboard...", big=True)
        lab.cancel_all()
        lab.wait_nodes_idle(60)
        now = datetime.now(cloud.IST)
        key = cloud.seed(now, timelapse.peak_pseudo_hours(now, timelapse.choose_boundary(now)), step=cloud.timedelta(minutes=1), hours=4)
        (cloud.RUN / "site.key").write_text(key)
        for f in ("state.db", "audit.log"):
            (cloud.RUN / f).unlink(missing_ok=True)
        sid = cloud.site_id()
        procs["cloud"] = cloud.start("demo_cloud_main.py")
        if not args.no_vite:
            procs["vite"] = start_vite()
        say(f"Ready. Open {URL} and put it on screen.", big=True)
        if not args.auto:
            input("Press Enter to start the demo... ")

        # ---- the demo ----
        t0 = time.time()
        now = datetime.now(cloud.IST)
        boundary = timelapse.choose_boundary(now, 330 if args.capture else timelapse.MIN_LEAD_S)  # captures take time: leave more room
        cloud.set_peak(timelapse.peak_pseudo_hours(now, boundary))
        b_epoch = int(boundary.timestamp())
        say(f"Electricity is EXPENSIVE right now. It turns CHEAP at {boundary:%H:%M:%S} IST.", big=True)
        say("(A time-lapse: every two minutes stand for one tariff hour. Everything else is real: Slurm, the agent, the cloud.)")
        blockers = [lab.sbatch("bob", f"demo-busy{i}", qos="normal", gres=4, minutes=2, sleep_s=BLOCK_S) for i in (1, 2)]
        lab.until(lambda: all(lab.state_of(b) == "RUNNING" for b in blockers), 60, what="the busy cluster")
        jobs = {n: lab.sbatch("alice", f"demo-{n}", gres=g, minutes=1, sleep_s=40) for n, g in (("A", 2), ("B", 1), ("C", 1))}
        say("Three GPU jobs are waiting behind a busy cluster:", big=True)
        show("squeue", ["squeue", "-o", "%.8i %.9j %.10T %.11r"])

        say("SHADOW MODE: Wattshift watches and plans. It must not touch Slurm.", big=True)
        cloud.api("POST", f"/sites/{sid}/mode", {"mode": "shadow"})
        procs["agent"] = agentproc.start("autonomous")
        lab.until(lambda: all(cloud.decisions(str(j)) for j in jobs.values()), 90, what="the cloud to plan all three jobs")
        say("The cloud has planned all three, and Slurm is untouched (no start time set):")
        scontrol_lines(jobs["A"])
        if args.capture:
            capture("01-shadow")

        say("Now switch the site to AUTONOMOUS. Click it on the dashboard.", big=True)
        try:
            lab.until(lambda: cloud.site_row()["mode"] == "autonomous", (8 if not args.capture else 3) if args.auto else 45, what="the presenter's click")
        except TimeoutError:
            say("(switching it for you)")
            cloud.api("POST", f"/sites/{sid}/mode", {"mode": "autonomous"})
        lab.until(lambda: all((lab.epoch(lab.show(j).get("EligibleTime")) or 0) >= b_epoch for j in jobs.values()), 90, what="Slurm to hold all three jobs")
        say(f"Wattshift set their start time in Slurm: {boundary:%H:%M:%S}, the moment electricity turns cheap.")
        scontrol_lines(jobs["A"], "   <- Slurm will not start it before this time")

        lab.wait_finished(blockers, 120)
        say("The busy jobs are done. The GPUs are FREE, and Slurm still holds our jobs:", big=True)
        show("sinfo", ["sinfo", "-N", "-h", "-o", "%N %T"])
        show("squeue", ["squeue", "-o", "%.8i %.9j %.10T %.11r %S"])
        if args.capture:
            capture("02-held")

        say("Now stop the agent AND the cloud completely.", big=True)
        agentproc.stop(procs.pop("agent"))
        cloud.stop(procs.pop("cloud"))
        say("Both are off. Slurm alone keeps the promise:")
        show("squeue", ["squeue", "-o", "%.8i %.9j %.10T %.11r %S"])
        while (left := b_epoch - time.time()) > 0:
            say(f"  ...the jobs start in {int(left)} s")
            time.sleep(min(10, max(1, left)))
        lab.until(lambda: any(lab.state_of(j) in ("RUNNING", "COMPLETED") for j in jobs.values()), 90, what="the first job to start")
        say("The jobs just started, with nothing of ours running:", big=True)
        show("squeue", ["squeue", "-o", "%.8i %.9j %.10T %.11r %S"])
        show(f"sacct   (the set time was {boundary:%H:%M:%S}; Slurm starts a job at the next scheduling cycle after it, never before)",
             ["sacct", "-X", "-j", ",".join(str(j) for j in jobs.values()), "-o", "JobID,JobName%10,State%10,Start"])

        say("Bring the cloud back. It learns what really happened from Slurm's own records.", big=True)
        procs["cloud"] = cloud.start("demo_cloud_main.py")
        procs["agent"] = agentproc.start("autonomous")
        lab.until(lambda: all((cloud.managed(str(j)) or {}).get("saved") is not None for j in jobs.values()), 150, what="the measured saving")
        s = cloud.api("GET", f"/sites/{sid}/view")["summary"]
        say(f"Measured saving: Rs {s['saved']:.2f} ({s['pct_saved']}% lower than starting when Slurm would have), across {s['jobs_measured']} jobs.", big=True)
        say("The percentage is what carries over to real jobs: these demo jobs run seconds, so the rupees are pennies.")
        say(f"That is about {time.time() - t0:.0f} seconds from the first job to the measured result.")
        if args.capture:
            capture("04-measured")
        return 0
    except Exception:
        traceback.print_exc()
        say("The live demo failed. Use the backup pack: docs/demo/storyboard.md, the screenshots, and the transcript.", big=True)
        return 1
    finally:
        for name, p in list(procs.items()):
            if p is not None:
                try:
                    (agentproc.stop if name == "agent" else cloud.stop)(p)
                except Exception:
                    pass
        lab.cancel_all()
        DEMO.mkdir(parents=True, exist_ok=True)
        (DEMO / "transcript.txt").write_text("\n".join(LINES), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
```

`cloud.timedelta` works because `cloud.py` imports `timedelta`; if it does not resolve, use `from datetime import timedelta` in this file and replace `cloud.timedelta(minutes=1)` with `timedelta(minutes=1)`.

- [ ] **Step 2: Rehearse it with the dashboard on screen**

Make sure the lab is up (Task 0). Open the built-in browser on `http://localhost:5174/#/live` after the script prints "Ready", then run in the background:

```powershell
cd C:\Users\aakur\OneDrive\Desktop\WattShift
.\backend\.venv\Scripts\python e2e\demo.py --auto
```

While it runs, read its output and take a screenshot of the dashboard at each of these moments (the narration lines tell you when): (1) three jobs waiting, "would hold" after shadow planning; (2) after the switch to autonomous, jobs "Held" with the timeline showing them moved right into the cheap zone; (3) the cloud stopped, with the "cloud is not reachable" message and the jobs still Held; (4) the end, with done jobs, run bars and the measured ₹ figure. Save them as `docs/demo/screens/01-shadow.png`, `02-held.png`, `03-cloud-stopped.png`, `04-measured.png`. Expected: the whole run finishes in about 5 minutes with no error and the last line reports a measured saving above zero. Then run it once more **without** `--auto` and click the Autonomous switch on the dashboard yourself to confirm the manual path works.

Fix what the rehearsal shows. Typical first-run problems: a Slurm command output that is hard to read on a projector (widen the `squeue` format), the dashboard polling lagging behind the narration (the page polls every 2 seconds; lengthen a pause rather than speeding up polling), `npx.cmd` not found (start the dashboard server by hand with `$env:VITE_API_TARGET='http://127.0.0.1:8100'; npx vite --port 5174 --strictPort` and pass `--no-vite`).

- [ ] **Step 3: Write the backup pack**

Create `docs/demo/storyboard.md` with these sections, in plain language: (1) **The five-minute story** (the table above, with one sentence of narration per row and the exact wording to say the time-lapse caveat out loud); (2) **How to run it** (prerequisites: Docker Desktop running, `python e2e\lab.py up` done once; `python e2e\demo.py`; open `http://localhost:5174/#/live`; press Enter; click Autonomous when asked); (3) **What is real and what is a time-lapse** (real: Slurm, the agent, the cloud's planning and measurement; time-lapse: the tariff clock; modeled: power and ₹); (4) **If something goes wrong**, three fallbacks in order: run `python -m scripts.simulate_agent` from `backend` against the normal API to drive the Live page with no Docker (about 25 seconds of scripted events), show the screenshots in `docs/demo/screens/` in order, or read the transcript in `docs/demo/transcript.txt`; (5) **Likely questions and honest answers** (is the saving measured? partly: times are real, power is modeled; what if the cloud is down? Slurm keeps enforcing set start times, `release-all` works without the cloud; what does it need in a customer's Slurm? an Operator account, so there is an allow-list and an audit log).

- [ ] **Step 4: Confirm nothing else broke**

Run the three suites: `cd backend; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider`, `cd frontend; npx vitest run`, `cd agent; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider`, and `.\backend\.venv\Scripts\python -m pytest e2e/ -q -p no:cacheprovider`. Expected: all green.

---

### Task 7: Wrap-up

**Files:**
- Modify: `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md`, `C:\Users\aakur\.claude\plans\analyse-all-the-files-snuggly-haven.md`

- [ ] **Step 1: Update the spec.** Section 13: the dashboard change is done (Live cluster view: site, per-job states, mode, kill switch, shift timeline, measured versus planned saving, activity feed). Section 15: mark step 4 "**Measurement and dashboard: DONE <date>**" with the plan path. Section 17: criterion 6 "verified <date>: the Live page shows each job's state and the measured saving, and the figure matches a hand calculation (`backend/tests/test_measure.py`)". Add one paragraph under section 16 noting the demo's time-lapse device and that it is a presentation aid, not a test of the real tariff.
- [ ] **Step 2: Progress note.** Append to the project plan file under "AUTOMATION ARC": what was built (measurement, site read model and endpoints, Live cluster page, time-lapse demo mode, backup pack), test counts, how to run the demo (`python e2e\demo.py`), and what remains: the long real-tariff end-to-end run (`python e2e\run.py --mode full`), and Phase 8 hosting.
- [ ] **Step 3: Report to the user in plain words:** what the audience will see in five minutes and what is real versus time-lapse; the rehearsal result (total time, any hiccups); where the backup pack is; and that nothing is committed.

---

## Self-review

**Spec coverage.** Section 10 (baseline, actual, power, cost, "modeled until checked against a bill"): Task 1. Section 13 dashboard (site picker, per-job states, mode; the manual form stays only in demo mode): Tasks 2 to 4 (the manual Submit form is untouched and remains for the old demo). Section 17 criterion 6 (states and modeled saving shown, matching a hand calculation): Tasks 1, 2, 4. The demo device, the time-lapse, is stated as a presentation aid in three places (Task 5 docstring, on-screen narration in Task 6, spec note in Task 7).

**Placeholders.** None. Where a value depends on a rehearsal (screenshot moments, pauses), the step says what to observe and what to adjust.

**Type consistency.** `display_state` keys (Task 2) equal the `DisplayState` union and the `STATE` map keys (Task 3); the view's field names (`summary.saved`, `summary.potential`, `jobs[].display`, `activity[].text`, `zones[].zone`) match the TypeScript types and the components; `cloud.seed(..., step, hours)`, `cloud.start(launcher)` and `cloud.set_peak` (Task 5) are the calls `demo.py` makes; `timelapse.choose_boundary` and `peak_pseudo_hours` (Task 5) match their uses.

**Known limits, stated once.** The live demo needs Docker Desktop running and the lab up, which takes minutes once; it uses a compressed tariff clock; power and ₹ are modeled; the Live page shows only the first 200 jobs and 40 activity lines; read endpoints are open in development (Phase 8 gates them); a `git` commit is not made.

