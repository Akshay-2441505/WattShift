# Slurm agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A small program that runs inside a customer's network next to their Slurm cluster: it finds the jobs their flex rules cover, reports them to the Wattshift cloud every ~30 s, and (in autonomous mode) sets each job's start time to the cloud's decision. It never holds, cancels or edits a job in any other way, and it does nothing harmful if the cloud or the agent dies.

**Architecture:** Plain Python 3.10+ package `wattshift_agent` in `agent/`, one runtime dependency (PyYAML). A `Runner` is the only code that touches Slurm: it enforces a command allow-list, fixes the environment (`TZ=UTC`, epoch times) and writes an audit line per command. Pure parsers turn Slurm's text into small dataclasses. A local SQLite file remembers what the agent has seen and applied (needed for override detection and for retrying reports). One cycle = read Slurm, update the record, `POST /agent/v1/sync`, apply the answer. Tests use an in-process `FakeSlurm` that answers the exact commands the agent sends, in the text formats recorded in Spike 0.

**Tech Stack:** Python 3.13 (works on 3.10+), stdlib (`subprocess`, `sqlite3`, `urllib`, `argparse`, `logging`), PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md` (sections 5, 6, 8, 9, 13, 14). The cloud side is already built (`docs/superpowers/plans/2026-09-20-cloud-core.md`). Evidence for every Slurm command and format: `docs/superpowers/spikes/2026-09-19-slurm-spike-findings.md` and `agent/tests/fixtures/slurm/`.

## Global Constraints

- **Allow-list (spec section 8):** the agent may run only `squeue ...`, `sacct ...`, `scontrol show job <digits>` and `scontrol update JobId=<digits> StartTime=<value>`. `<value>` is `now` (always allowed: a release, which only undoes the agent's own earlier deferral) or an absolute UTC time `YYYY-MM-DDTHH:MM:SS` (a deferral: allowed only when `mode: autonomous`). Nothing else, ever: no `scancel`, no `hold`, no `Comment=`, no other update field. Every command is written to the audit log.
- **Times:** epoch seconds inside the agent; UTC ISO strings ending in `Z` on the wire. Every Slurm command runs with `TZ=UTC` and `SLURM_TIME_FORMAT=%s` (Spike 0: Slurm reads absolute times in the client's time zone and rejects `@epoch`).
- **Fail open:** cloud unreachable, agent crash, Slurm command failure: no job is changed. A start time already set stays set and Slurm enforces it.
- **Only flex jobs:** a job that matches no rule is never recorded, never read beyond the one `squeue` line, and never sent. A rule with `max_wait: 0` explicitly excludes what it matches.
- **Privacy:** only these facts leave the site: job id, state, submit/start/end times, GPU count, time limit, max wait, predicted start, two flags. Never user, account, job name, command line or script.
- **Shadow mode** has no code path that sets a future start time. The one write it may do is `StartTime=now` on a job the agent itself deferred earlier (undoing its own change after a mode switch).
- **The cloud is authoritative for planning; the agent is authoritative for Slurm.** The agent still refuses a decision that is beyond its own sanity bound (`first_seen + 2 x max_wait + 5 min`), targets a job that is no longer pending, or belongs to a job it stopped managing.
- **No commits:** the repo has no commits yet. Commit only when the user asks.
- Existing tests must still pass: `cd backend; .\.venv\Scripts\python -m pytest -q` (246 at the end of the cloud-core plan).

## File structure

```
agent/
  pyproject.toml                     package, one dependency, console script
  README.md                          install, config, Slurm permissions, what the agent runs
  agent.example.yaml                 documented example config
  wattshift_agent/
    __init__.py                      __version__
    __main__.py                      python -m wattshift_agent
    config.py                        YAML -> Config, Rule; parse_minutes; ConfigError
    rules.py                         max_wait_for(rules, job)
    parse.py                         pure parsers: squeue / scontrol / sacct text -> dataclasses
    audit.py                         rotating JSON-lines audit logger
    runner.py                        check_command (allow-list), SubprocessRunner, SlurmError
    slurm.py                         Slurm facade: queue(), detail(), finished(), set_start(), release()
    state.py                         SQLite record: Tracked jobs, outboxes, meta
    cycle.py                         observe(), build_body(), run_once()
    applier.py                       apply_decision(), release_all()
    client.py                        Cloud: post_sync(), health(); CloudError
    cli.py                           run / release-all / status / check
  tests/
    conftest.py                      shared fixtures (cfg, fake, slurm, state, cloud)
    fakeslurm.py                     in-process Slurm stand-in (test helper)
    fakecloud.py                     in-process cloud stand-in (test helper)
    fake_slurm_cli.py                tiny real executable used to test the subprocess runner
    fixtures/raw/*.txt               raw command outputs the parser tests read
    fixtures/contract/sync_all_kinds.json   one request body of every kind, shared with the backend test
    test_*.py
backend/tests/test_agent_contract.py the cloud accepts the agent's contract body
```

## Contract used (already implemented in the cloud)

Request `POST /agent/v1/sync` (header `X-Site-Key`): `{agent_version, mode, sent_at, jobs:[{ref, state, submit_time, gpus, time_limit_min, max_wait_min, predicted_start, start_time, end_time, override, skipped_reason}], applied:[{ref, start_at, ok, error}], released:[ref], release_all}`. Response: `{decisions:[{ref, start_at}], release_all, next_poll_s}`. `state` is one of `PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT, OTHER`.

## Slurm commands and their formats (what the parsers rely on)

| Purpose | Exact command | Output used |
|---|---|---|
| List | `squeue -h -t PENDING,RUNNING -o "%i\|%T\|%u\|%a\|%q\|%P\|%V\|%S\|%r\|%j"` | one pipe-separated line per job: id, state, user, account, qos, partition, submit (epoch), start (epoch or `N/A`), reason, name (last, so a `\|` in a name survives) |
| Detail (once per new flex job, and to check our own deferrals) | `scontrol show job <id>` | `key=value` tokens: `JobState`, `Reason`, `Dependency`, `Restarts`, `TimeLimit=HH:MM:SS`, `SubmitTime`, `EligibleTime`, `StartTime` (epoch or `Unknown`), `ReqTRES`, `TresPerJob`, `TresPerNode`, `NumNodes`, `ArrayJobId` |
| Finished | `sacct -j <ids> -P -X -n -S now-14days -o JobID,State,Start,End,ElapsedRaw,AllocTRES` | pipe rows; `Start` may be `None` (cancelled while pending) |
| Defer | `scontrol update JobId=<id> StartTime=<UTC ISO>` | nothing; the agent re-reads `EligibleTime` to confirm |
| Release | `scontrol update JobId=<id> StartTime=now` | nothing |

`EligibleTime` is what the agent compares to detect a user change, because `StartTime` also moves when Slurm's own prediction moves (Spike 0: updating a start time moves `EligibleTime` to the same instant). Formats seen directly in Spike 0: pipe rows with epoch times, `CANCELLED by 2001`, `Start=None`, `Reason=Dependency`, `Restarts=1`, `ArrayJobId=41 ArrayTaskId=1-3`, `TresPerNode=gres/gpu:3`, `TimeLimit=00:03:00`. **Not seen directly** (to be confirmed on the real Docker Slurm by the end-to-end plan; each is isolated in one function so a fix is local): the exact `squeue -o` field set, `sacct -S now-14days`, and `TresPerJob`.

---

### Task 1: Package scaffold

**Files:**
- Create: `agent/pyproject.toml`, `agent/wattshift_agent/__init__.py`, `agent/tests/__init__.py` (empty, so tests can `from tests.fakeslurm import ...`), `agent/tests/test_smoke.py`

**Interfaces:**
- Produces: importable package `wattshift_agent` with `__version__ == "0.1.0"`; a venv at `agent/.venv` with the package installed in editable mode.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_smoke.py`:

```python
import wattshift_agent


def test_package_has_a_version():
    assert wattshift_agent.__version__ == "0.1.0"
```

- [ ] **Step 2: Create the package files**

Create `agent/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "wattshift-agent"
version = "0.1.0"
description = "Sets flexible Slurm jobs' start times to cheap grid hours, from inside the customer's network."
requires-python = ">=3.10"
dependencies = ["PyYAML>=6"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
wattshift-agent = "wattshift_agent.cli:main"

[tool.setuptools.packages.find]
include = ["wattshift_agent*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `agent/wattshift_agent/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Make the venv and confirm the test passes**

Run:

```powershell
cd agent
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest -q
```

Expected: `1 passed`. (If `pip` cannot reach PyPI, stop and tell the user; PyYAML is the only download.)

---

### Task 2: Config and rules

**Files:**
- Create: `agent/wattshift_agent/config.py`, `agent/wattshift_agent/rules.py`, `agent/tests/test_config.py`, `agent/tests/test_rules.py`

**Interfaces:**
- Produces:
  - `config.ConfigError(ValueError)`, `config.parse_minutes(text) -> int` (`"90m"`, `"12h"`, `"2d"`)
  - `config.Rule(exact: tuple[tuple[str, str], ...], name_re: re.Pattern | None, max_wait_min: int)`
  - `config.Config(cloud_url, site_key, mode, rules, state_path, audit_path, poll_seconds=30, slurm_prefix=())` (frozen dataclass)
  - `config.load_config(path, environ=None) -> Config`
  - `rules.max_wait_for(rules, job) -> int | None` where `job` has attributes `qos, partition, user, account, name`. First matching rule wins; `None` means "do not touch" (no rule matched, or the matching rule has `max_wait: 0`).

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_config.py`:

```python
import pytest

from wattshift_agent.config import ConfigError, load_config, parse_minutes

GOOD = """
cloud:
  url: https://api.example.test/
  key_file: {key_file}
mode: autonomous
poll_seconds: 45
state_file: st.db
audit_file: {audit}
slurm:
  prefix: [ssh, headnode]
rules:
  - match: {{ qos: flex }}
    max_wait: 24h
  - match: {{ partition: batch-infer, name: "^eval-.*" }}
    max_wait: 12h
default: none
"""


def write(tmp_path, text, key="wsk_abc\n"):
    (tmp_path / "site.key").write_text(key)
    p = tmp_path / "agent.yaml"
    p.write_text(text.format(key_file=tmp_path / "site.key", audit=tmp_path / "a.log"))
    return p


@pytest.mark.parametrize("text, want", [("90m", 90), ("12h", 720), ("2d", 2880), (" 5 h ", 300), ("0h", 0)])
def test_durations(text, want):
    assert parse_minutes(text) == want


@pytest.mark.parametrize("text", ["", "12", "h", "1.5h", "-2h", "12 hours", "1w"])
def test_bad_durations(text):
    with pytest.raises(ConfigError):
        parse_minutes(text)


def test_a_full_config_loads(tmp_path):
    cfg = load_config(write(tmp_path, GOOD), environ={})
    assert cfg.cloud_url == "https://api.example.test" and cfg.site_key == "wsk_abc"
    assert cfg.mode == "autonomous" and cfg.poll_seconds == 45 and cfg.slurm_prefix == ("ssh", "headnode")
    assert cfg.state_path.name == "st.db" and cfg.state_path.parent == tmp_path.resolve()  # relative paths sit next to the config
    assert [r.max_wait_min for r in cfg.rules] == [1440, 720]
    assert cfg.rules[0].exact == (("qos", "flex"),) and cfg.rules[0].name_re is None
    assert cfg.rules[1].exact == (("partition", "batch-infer"),) and cfg.rules[1].name_re.search("eval-7")


def test_the_environment_key_beats_the_key_file(tmp_path):
    cfg = load_config(write(tmp_path, GOOD), environ={"WATTSHIFT_SITE_KEY": " wsk_env "})
    assert cfg.site_key == "wsk_env"


def test_mode_defaults_to_shadow_and_paths_default_next_to_the_config(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("cloud: {url: 'http://x'}\nrules: []\n")
    cfg = load_config(p, environ={"WATTSHIFT_SITE_KEY": "k"})
    assert cfg.mode == "shadow" and cfg.rules == () and cfg.poll_seconds == 30
    assert cfg.state_path.name == "state.db" and cfg.audit_path.name == "audit.log"


@pytest.mark.parametrize(
    "patch, msg",
    [
        ("mode: yolo\n", "mode must be"),
        ("poll_seconds: 5\n", "poll_seconds"),
        ("poll_seconds: fast\n", "poll_seconds"),
        ("default: allow\n", "default must be none"),
        ("colour: red\n", "unknown setting"),
        ("slurm: {shell: bash}\n", "slurm takes only prefix"),
        ("rules:\n  - match: {qoss: flex}\n    max_wait: 1h\n", "unknown field"),
        ("rules:\n  - match: {}\n    max_wait: 1h\n", "at least one field"),
        ("rules:\n  - match: {qos: flex}\n", "exactly two keys"),
        ("rules:\n  - match: {qos: flex}\n    max_wait: soon\n", "bad duration"),
        ("rules:\n  - match: {name: '('}\n    max_wait: 1h\n", "regular expression"),
        ("rules: nope\n", "rules must be a list"),
    ],
)
def test_mistakes_are_named(tmp_path, patch, msg):
    p = tmp_path / "agent.yaml"
    p.write_text("cloud: {url: 'http://x'}\n" + patch)
    with pytest.raises(ConfigError, match=msg):
        load_config(p, environ={"WATTSHIFT_SITE_KEY": "k"})


def test_a_key_inside_the_file_is_refused(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("cloud: {url: 'http://x', key: wsk_secret}\n")
    with pytest.raises(ConfigError, match="never goes in this file"):
        load_config(p, environ={})


def test_no_key_anywhere_is_an_error(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("cloud: {url: 'http://x'}\n")
    with pytest.raises(ConfigError, match="no site key"):
        load_config(p, environ={})


def test_bad_url_and_missing_file(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("cloud: {url: 'api.example.test'}\n")
    with pytest.raises(ConfigError, match="http"):
        load_config(p, environ={"WATTSHIFT_SITE_KEY": "k"})
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml", environ={})
```

Create `agent/tests/test_rules.py`:

```python
import re
from types import SimpleNamespace

from wattshift_agent.config import Rule
from wattshift_agent.rules import max_wait_for


def job(**kw):
    base = dict(qos="normal", partition="cpu", user="alice", account="labs", name="train")
    return SimpleNamespace(**{**base, **kw})


FLEX = Rule((("qos", "flex"),), None, 1440)
EVAL = Rule((("partition", "batch-infer"),), re.compile("^eval-.*"), 720)


def test_a_job_that_matches_no_rule_is_never_touched():
    assert max_wait_for((FLEX, EVAL), job()) is None
    assert max_wait_for((), job(qos="flex")) is None


def test_exact_fields_must_all_match():
    assert max_wait_for((FLEX,), job(qos="flex")) == 1440
    assert max_wait_for((FLEX,), job(qos="Flex")) is None  # exact, not case-insensitive


def test_name_is_a_regex_searched_anywhere_and_combined_with_the_other_fields():
    assert max_wait_for((EVAL,), job(partition="batch-infer", name="eval-7")) == 720
    assert max_wait_for((EVAL,), job(partition="batch-infer", name="my-eval-7")) is None  # ^ anchors it
    assert max_wait_for((EVAL,), job(partition="cpu", name="eval-7")) is None  # partition must match too


def test_first_matching_rule_wins():
    both = job(qos="flex", partition="batch-infer", name="eval-1")
    assert max_wait_for((FLEX, EVAL), both) == 1440
    assert max_wait_for((EVAL, FLEX), both) == 720


def test_max_wait_zero_excludes_the_job_and_stops_the_search():
    protect = Rule((("user", "alice"),), None, 0)
    assert max_wait_for((protect, FLEX), job(qos="flex", user="alice")) is None
    assert max_wait_for((protect, FLEX), job(qos="flex", user="bob")) == 1440
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_config.py tests/test_rules.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'wattshift_agent.config'`.

- [ ] **Step 3: Write `config.py`**

Create `agent/wattshift_agent/config.py`:

```python
"""Agent configuration: one YAML file the operator edits once (spec section 5)."""
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

MODES = ("shadow", "autonomous")
MATCH_KEYS = ("qos", "partition", "name", "user", "account")
TOP_KEYS = {"cloud", "mode", "poll_seconds", "state_file", "audit_file", "slurm", "rules", "default"}
_DURATION = re.compile(r"^\s*(\d+)\s*([mhd])\s*$")
_UNIT_MIN = {"m": 1, "h": 60, "d": 1440}


class ConfigError(ValueError):
    """The config file is wrong; the message says what to fix."""


def parse_minutes(text) -> int:
    m = _DURATION.match(str(text))
    if not m:
        raise ConfigError(f"bad duration {text!r}: write it like 90m, 12h or 2d")
    return int(m.group(1)) * _UNIT_MIN[m.group(2)]


@dataclass(frozen=True)
class Rule:
    exact: tuple  # (("qos", "flex"), ...): fields that must be equal
    name_re: "re.Pattern | None"  # `name` is a regular expression, searched anywhere in the job name
    max_wait_min: int  # 0 = never touch jobs that match this rule


@dataclass(frozen=True)
class Config:
    cloud_url: str
    site_key: str
    mode: str
    rules: tuple
    state_path: Path
    audit_path: Path
    poll_seconds: int = 30
    slurm_prefix: tuple = ()  # e.g. ("ssh", "headnode"): how to reach Slurm when it is not on this machine


def _rule(i: int, raw) -> Rule:
    where = f"rules[{i}]"
    if not isinstance(raw, dict) or set(raw) != {"match", "max_wait"}:
        raise ConfigError(f"{where} must have exactly two keys: match and max_wait")
    match = raw["match"]
    if not isinstance(match, dict) or not match:
        raise ConfigError(f"{where}.match must list at least one field ({', '.join(MATCH_KEYS)})")
    bad = set(match) - set(MATCH_KEYS)
    if bad:
        raise ConfigError(f"{where}.match has unknown field(s) {sorted(bad)}; allowed: {', '.join(MATCH_KEYS)}")
    name_re = None
    if "name" in match:
        try:
            name_re = re.compile(str(match["name"]))
        except re.error as e:
            raise ConfigError(f"{where}.match.name is not a valid regular expression: {e}")
    exact = tuple(sorted((k, str(v)) for k, v in match.items() if k != "name"))
    return Rule(exact, name_re, parse_minutes(raw["max_wait"]))


def load_config(path, environ=None) -> Config:
    env = os.environ if environ is None else environ
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}")
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}")
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain settings, not {type(raw).__name__}")
    unknown = set(raw) - TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown setting(s): {', '.join(sorted(unknown))}")

    cloud = raw.get("cloud") or {}
    if not isinstance(cloud, dict) or set(cloud) - {"url", "key_file"}:
        raise ConfigError("cloud takes only url and key_file (the site key never goes in this file: use key_file or WATTSHIFT_SITE_KEY)")
    url = str(cloud.get("url", "")).rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ConfigError("cloud.url must start with http:// or https://")
    key = env.get("WATTSHIFT_SITE_KEY", "").strip()
    if not key and cloud.get("key_file"):
        try:
            key = Path(cloud["key_file"]).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            raise ConfigError(f"cannot read cloud.key_file: {e}")
    if not key:
        raise ConfigError("no site key: set WATTSHIFT_SITE_KEY or cloud.key_file")

    mode = raw.get("mode", "shadow")
    if mode not in MODES:
        raise ConfigError(f"mode must be one of {MODES}, not {mode!r}")
    poll = raw.get("poll_seconds", 30)
    if not isinstance(poll, int) or isinstance(poll, bool) or not 10 <= poll <= 300:
        raise ConfigError("poll_seconds must be a whole number from 10 to 300")
    if raw.get("default", "none") != "none":
        raise ConfigError("default must be none: a job that matches no rule is never touched")
    slurm = raw.get("slurm") or {}
    if not isinstance(slurm, dict) or set(slurm) - {"prefix"}:
        raise ConfigError("slurm takes only prefix")
    prefix = slurm.get("prefix") or []
    if not isinstance(prefix, list) or not all(isinstance(p, str) for p in prefix):
        raise ConfigError("slurm.prefix must be a list of words")
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise ConfigError("rules must be a list")

    here = path.resolve().parent

    def where(name: str, default: str) -> Path:
        p = Path(raw.get(name, default)).expanduser()
        return p if p.is_absolute() else here / p

    return Config(
        cloud_url=url, site_key=key, mode=mode, rules=tuple(_rule(i, r) for i, r in enumerate(rules_raw)),
        state_path=where("state_file", "state.db"), audit_path=where("audit_file", "audit.log"),
        poll_seconds=poll, slurm_prefix=tuple(prefix),
    )
```

Create `agent/wattshift_agent/rules.py`:

```python
"""Which jobs are flexible, and for how long may they wait (spec section 5)."""


def max_wait_for(rules, job) -> int | None:
    """Minutes the job may wait, or None if Wattshift must not touch it. First matching rule wins; a rule with
    max_wait 0 is an explicit exclusion. `job` needs qos, partition, user, account and name attributes."""
    for r in rules:
        if all(getattr(job, k) == v for k, v in r.exact) and (r.name_re is None or r.name_re.search(job.name)):
            return r.max_wait_min or None
    return None
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_config.py tests/test_rules.py -q`
Expected: all pass.

---

### Task 3: Parsers for Slurm's output

**Files:**
- Create: `agent/wattshift_agent/parse.py`, `agent/tests/test_parse.py`, and three raw fixture files under `agent/tests/fixtures/raw/`

**Interfaces:**
- Produces (all in `wattshift_agent.parse`):
  - `ParseError(ValueError)`; `TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OTHER")`
  - `epoch(text) -> int | None` (digits only; `N/A`, `Unknown`, `None`, empty all give `None`)
  - `normalize_state(raw) -> str` (one of `PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT, OTHER`, matched by prefix)
  - `QueueRow(ref, state, user, account, qos, partition, submit, start, reason, name)` and `parse_queue(text) -> list[QueueRow]`
  - `parse_kv(text) -> dict[str, str]` (first occurrence of a key wins)
  - `gpus_from(kv) -> int`, `limit_minutes(text) -> int | None`
  - `JobDetail(ref, state, reason, dependency, restarts, array, gpus, time_limit_min, submit, eligible, start)` and `parse_detail(text, ref) -> JobDetail`
  - `AcctRow(ref, state, start, end, elapsed_s, gpus)` and `parse_acct(text) -> list[AcctRow]`

- [ ] **Step 1: Create the raw fixtures**

These are raw command outputs in the formats recorded in Spike 0 (real rows and lines reused where they exist).

Create `agent/tests/fixtures/raw/squeue_queue.txt` (five jobs; the third has a `|` in its name; the fourth is an array; the fifth is dependent):

```
48211|PENDING|alice|labs|flex|cpu|1789819300|N/A|None|train-demo
48212|PENDING|bob|labs|flex|cpu|1789819310|1789826654|BeginTime|eval-a
48213|RUNNING|alice|labs|normal|cpu|1789819000|1789819100|None|nightly build | v2
41_[1-3]|PENDING|alice|labs|flex|cpu|1789819200|1789826000|None|array-job
43|PENDING|alice|labs|flex|cpu|1789819400|N/A|Dependency|after-42
```

Create `agent/tests/fixtures/raw/scontrol_deferred.txt`:

```
JobId=15 JobName=wrap
   UserId=alice(2001) GroupId=alice(2001) MCS_label=N/A
   Priority=4294901744 Nice=0 Account=labs QOS=flex
   JobState=PENDING Reason=BeginTime Dependency=(null)
   Requeue=1 Restarts=0 BatchFlag=1 Reboot=0 ExitCode=0:0
   RunTime=00:00:00 TimeLimit=01:30:00 TimeMin=N/A
   SubmitTime=1789819454 EligibleTime=1789826654 AccrueTime=1789826654
   StartTime=1789826654 EndTime=1789832054 Deadline=N/A
   Partition=cpu AllocNode:Sid=slurmctld:2210
   NumNodes=1 NumCPUs=1 NumTasks=1 CPUs/Task=1 ReqB:S:C:T=0:0:*:*
   ReqTRES=cpu=1,mem=11815M,node=1,billing=1
   TresPerNode=gres/gpu:3
   SubmitLine=sbatch --parsable -p cpu --qos=flex --gres=gpu:3 -t 90 --wrap=sleep 5
```

Create `agent/tests/fixtures/raw/sacct_rows.txt`:

```
23|COMPLETED|1789819300|1789819322|22|billing=2,cpu=2,gres/gpu=3,mem=11815M,node=1
24|FAILED|1789819344|1789819345|1|billing=1,cpu=1,mem=11815M,node=1
25|CANCELLED by 0|None|1789819352|0|
26|TIMEOUT|1789819344|1789819429|85|billing=1,cpu=1,mem=11815M,node=1
27|CANCELLED by 0|1789819347|1789819352|5|billing=1,cpu=1,mem=11815M,node=1
28|RUNNING|1789819500|Unknown|0|billing=1,cpu=1,mem=1M,node=1
```

- [ ] **Step 2: Write the failing test**

Create `agent/tests/test_parse.py`:

```python
from pathlib import Path

import pytest

from wattshift_agent import parse

RAW = Path(__file__).parent / "fixtures" / "raw"


def raw(name):
    return (RAW / name).read_text(encoding="utf-8")


def detail_text(**over):
    base = {"JobId": "7", "JobState": "PENDING", "Reason": "None", "Dependency": "(null)", "Restarts": "0",
            "TimeLimit": "00:10:00", "SubmitTime": "100", "EligibleTime": "100", "StartTime": "Unknown"}
    base.update(over)
    return " ".join(f"{k}={v}" for k, v in base.items())


def test_queue_rows():
    rows = parse.parse_queue(raw("squeue_queue.txt"))
    assert [r.ref for r in rows] == ["48211", "48212", "48213", "41_[1-3]", "43"]
    a, b, c, arr, dep = rows
    assert (a.state, a.user, a.account, a.qos, a.partition, a.submit, a.start, a.reason, a.name) == (
        "PENDING", "alice", "labs", "flex", "cpu", 1789819300, None, "None", "train-demo")
    assert b.start == 1789826654 and b.reason == "BeginTime"
    assert c.state == "RUNNING" and c.name == "nightly build | v2"  # a | inside the job name survives
    assert arr.ref == "41_[1-3]" and dep.reason == "Dependency"


def test_empty_queue_and_short_line():
    assert parse.parse_queue("") == [] and parse.parse_queue("\n\n") == []
    with pytest.raises(parse.ParseError):
        parse.parse_queue("1|PENDING|alice")


@pytest.mark.parametrize(
    "state, want",
    [("PENDING", "PENDING"), ("RUNNING", "RUNNING"), ("COMPLETING", "RUNNING"), ("COMPLETED", "COMPLETED"),
     ("CANCELLED by 2001", "CANCELLED"), ("CANCELLED+", "CANCELLED"), ("FAILED", "FAILED"), ("TIMEOUT", "TIMEOUT"),
     ("NODE_FAIL", "OTHER"), ("OUT_OF_MEMORY", "OTHER"), ("", "OTHER")],
)
def test_states_are_matched_by_prefix(state, want):
    assert parse.normalize_state(state) == want


def test_epoch():
    assert parse.epoch("1789819300") == 1789819300 and parse.epoch("0") == 0
    for junk in ("N/A", "Unknown", "None", "", None, "2026-09-19T12:00:00"):
        assert parse.epoch(junk) is None


def test_kv_first_key_wins_and_ignores_stray_words():
    assert parse.parse_kv("A=1 B=x y C=3 A=9") == {"A": "1", "B": "x", "C": "3"}


def test_detail_of_a_deferred_job():
    d = parse.parse_detail(raw("scontrol_deferred.txt"), "15")
    assert (d.state, d.reason, d.dependency, d.restarts, d.array) == ("PENDING", "BeginTime", False, 0, False)
    assert d.gpus == 3 and d.time_limit_min == 90
    assert (d.submit, d.eligible, d.start) == (1789819454, 1789826654, 1789826654)


def test_detail_flags():
    assert parse.parse_detail(detail_text(Dependency="afterok:42(unfulfilled)"), "7").dependency is True
    assert parse.parse_detail(detail_text(Restarts="1"), "7").restarts == 1
    assert parse.parse_detail(detail_text(ArrayJobId="7"), "7").array is True
    assert parse.parse_detail(detail_text(Restarts="lots"), "7").restarts == 0
    assert parse.parse_detail(detail_text(), "7").start is None  # Unknown


def test_an_error_message_is_not_a_job():
    with pytest.raises(parse.ParseError):
        parse.parse_detail("slurm_load_jobs error: Invalid job id specified", "9")


@pytest.mark.parametrize(
    "fields, gpus",
    [
        ("ReqTRES=cpu=1,gres/gpu=4 TresPerNode=gres/gpu:9", 4),  # accounting total wins when the cluster tracks it
        ("TresPerJob=gres/gpu:8", 8),
        ("TresPerNode=gres/gpu:3", 3),
        ("TresPerNode=gres/gpu:a100:4 NumNodes=2", 8),  # typed GPUs, per node x nodes
        ("TresPerNode=gres/gpu:3 NumNodes=2-4", 6),  # a node range counts its minimum
        ("TresPerNode=gres/gpu", 1),
        ("TresPerNode=gres/gpu:a100", 1),
        ("TresPerNode=gres/gpu:2,gres/shard:4", 2),
        ("TresPerNode=gres/gpumem:16G", 0),  # a different resource that merely starts with gpu
        ("ReqTRES=cpu=1,mem=1M", 0),
        ("", 0),
    ],
)
def test_gpu_counts(fields, gpus):
    assert parse.gpus_from(parse.parse_kv(fields)) == gpus


@pytest.mark.parametrize(
    "text, want",
    [("00:03:00", 3), ("01:30:00", 90), ("1-00:00:00", 1440), ("00:00:30", 1), ("00:01:01", 2),
     ("UNLIMITED", None), ("Partition_Limit", None), ("", None), ("5", None)],
)
def test_time_limit_minutes(text, want):
    assert parse.limit_minutes(text) == want


def test_accounting_rows():
    rows = {r.ref: r for r in parse.parse_acct(raw("sacct_rows.txt"))}
    assert rows["23"].state == "COMPLETED" and (rows["23"].start, rows["23"].end, rows["23"].elapsed_s) == (1789819300, 1789819322, 22)
    assert rows["23"].gpus == 3 and rows["24"].gpus is None  # only present when the cluster tracks gres/gpu
    assert rows["24"].state == "FAILED" and rows["26"].state == "TIMEOUT" and rows["26"].elapsed_s == 85
    assert rows["25"].state == "CANCELLED" and rows["25"].start is None and rows["25"].end == 1789819352
    assert rows["28"].state == "RUNNING" and rows["28"].end is None
    with pytest.raises(parse.ParseError):
        parse.parse_acct("1|COMPLETED")
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_parse.py -q`
Expected: FAIL, `ImportError: cannot import name 'parse'`.

- [ ] **Step 4: Write `parse.py`**

Create `agent/wattshift_agent/parse.py`:

```python
"""Pure parsers for the text Slurm prints. Times are epoch seconds because the agent sets SLURM_TIME_FORMAT=%s."""
import re
from dataclasses import dataclass


class ParseError(ValueError):
    """Slurm printed something the agent does not understand."""


TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OTHER")
_RUNNING = ("RUNNING", "COMPLETING", "CONFIGURING", "SUSPENDED", "RESIZING", "SIGNALING", "STAGE_OUT")
_KV = re.compile(r"(\S+?)=(\S*)")
_GPU = re.compile(r"gres/gpu(?![\w/])(?::[A-Za-z][\w.\-]*)?(?::(\d+))?")  # gres/gpu, gres/gpu:3, gres/gpu:a100:4
_GPU_TOTAL = re.compile(r"gres/gpu=(\d+)")
_LIMIT = re.compile(r"(?:(\d+)-)?(\d+):(\d+):(\d+)")


def epoch(text) -> int | None:
    t = (text or "").strip()
    return int(t) if t.isdigit() else None


def _int(text, default=0) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def normalize_state(raw: str) -> str:
    s = (raw or "").strip().upper()
    if s.startswith("PENDING"):
        return "PENDING"
    if s in _RUNNING:
        return "RUNNING"
    for full in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"):
        if s.startswith(full):  # "CANCELLED by 2001", "CANCELLED+"
            return full
    return "OTHER"


@dataclass(frozen=True)
class QueueRow:
    ref: str
    state: str
    user: str
    account: str
    qos: str
    partition: str
    submit: int | None
    start: int | None  # Slurm's estimate (or a user's --begin) for a pending job; the real start for a running one
    reason: str
    name: str


def parse_queue(text: str) -> list[QueueRow]:
    """Lines of `%i|%T|%u|%a|%q|%P|%V|%S|%r|%j`; the name is last so a | inside it survives."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        p = line.split("|", 9)
        if len(p) != 10:
            raise ParseError(f"unexpected squeue line: {line[:80]!r}")
        ref, state, user, account, qos, partition, submit, start, reason, name = p
        rows.append(QueueRow(ref, normalize_state(state), user, account, qos, partition, epoch(submit), epoch(start), reason, name))
    return rows


def parse_kv(text: str) -> dict[str, str]:
    """`scontrol show job` prints key=value tokens. The first occurrence of a key wins, so a later SubmitLine or
    Command containing key=value words cannot override the real fields."""
    out: dict[str, str] = {}
    for m in _KV.finditer(text):
        out.setdefault(m.group(1), m.group(2))
    return out


def _gpu_count(text: str) -> int:
    return sum(int(m.group(1) or 1) for m in _GPU.finditer(text))


def gpus_from(kv: dict[str, str]) -> int:
    """GPUs the job asked for. ReqTRES has the total when the cluster tracks gres/gpu in accounting; otherwise the
    live view has it as TresPerJob (total) or TresPerNode (per node)."""
    m = _GPU_TOTAL.search(kv.get("ReqTRES", ""))
    if m:
        return int(m.group(1))
    per_job = _gpu_count(kv.get("TresPerJob", ""))
    if per_job:
        return per_job
    per_node = _gpu_count(kv.get("TresPerNode", ""))
    if per_node:
        first = re.match(r"\d+", kv.get("NumNodes", "1") or "1")  # "2" or a range like "2-4": count the minimum
        return per_node * max(int(first.group(0)) if first else 1, 1)
    return 0


def limit_minutes(text: str) -> int | None:
    """scontrol prints TimeLimit as [D-]HH:MM:SS. UNLIMITED, Partition_Limit or anything else gives None (the cloud
    then leaves the job alone). Rounded up to whole minutes."""
    m = _LIMIT.fullmatch((text or "").strip())
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return max(1, -(-(((d * 24 + h) * 60 + mi) * 60 + s) // 60))


@dataclass(frozen=True)
class JobDetail:
    ref: str
    state: str
    reason: str
    dependency: bool
    restarts: int
    array: bool
    gpus: int
    time_limit_min: int | None
    submit: int | None
    eligible: int | None  # the begin time: what an update of StartTime sets
    start: int | None


def parse_detail(text: str, ref: str) -> JobDetail:
    kv = parse_kv(text)
    if "JobId" not in kv or "JobState" not in kv:
        raise ParseError(f"not a job description for {ref}: {text[:80]!r}")
    return JobDetail(
        ref=ref, state=normalize_state(kv["JobState"]), reason=kv.get("Reason", "None"),
        dependency=kv.get("Dependency", "(null)") not in ("(null)", "", "N/A"),
        restarts=_int(kv.get("Restarts")), array="ArrayJobId" in kv, gpus=gpus_from(kv),
        time_limit_min=limit_minutes(kv.get("TimeLimit", "")), submit=epoch(kv.get("SubmitTime")),
        eligible=epoch(kv.get("EligibleTime")), start=epoch(kv.get("StartTime")),
    )


@dataclass(frozen=True)
class AcctRow:
    ref: str
    state: str
    start: int | None  # None for a job cancelled while pending
    end: int | None
    elapsed_s: int | None
    gpus: int | None  # None unless the cluster tracks gres/gpu in accounting


def parse_acct(text: str) -> list[AcctRow]:
    """Rows of `JobID|State|Start|End|ElapsedRaw|AllocTRES` (sacct -P -X -n)."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        p = line.split("|")
        if len(p) != 6:
            raise ParseError(f"unexpected sacct line: {line[:80]!r}")
        ref, state, start, end, elapsed, alloc = p
        m = _GPU_TOTAL.search(alloc)
        rows.append(AcctRow(ref, normalize_state(state), epoch(start), epoch(end), epoch(elapsed), int(m.group(1)) if m else None))
    return rows
```

- [ ] **Step 5: Run it and confirm it passes**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_parse.py -q`
Expected: all pass.

---

### Task 4: The runner (allow-list), the Slurm facade and the fake Slurm

**Files:**
- Create: `agent/wattshift_agent/audit.py`, `agent/wattshift_agent/runner.py`, `agent/wattshift_agent/slurm.py`, `agent/tests/fakeslurm.py`, `agent/tests/fake_slurm_cli.py`, `agent/tests/test_runner.py`, `agent/tests/test_slurm.py`

**Interfaces:**
- Consumes: `parse` (Task 3).
- Produces:
  - `audit.make_audit_logger(path) -> logging.Logger`; `audit.event(log, name, **fields)` (a no-op when `log` is `None`)
  - `runner.ForbiddenCommand(Exception)`, `runner.SlurmError(Exception)`
  - `runner.check_command(argv, allow_defer) -> None`
  - `runner.SubprocessRunner(*, allow_defer, prefix=(), audit=None, timeout=30)` with `.run(argv) -> str`
  - `slurm.QUEUE_FORMAT`; `slurm.fmt_utc(epoch) -> "YYYY-MM-DDTHH:MM:SS"`; `slurm.Slurm(runner)` with `queue() -> list[QueueRow]`, `detail(ref) -> JobDetail | None` (`None` when Slurm no longer knows the job), `finished(refs) -> list[AcctRow]`, `set_start(ref, epoch)`, `release(ref)`
  - test helper `FakeSlurm(now=1_790_000_000, allow_defer=True)` with `.add(ref, **job)`, `.advance(seconds)`, `.cancel(ref)`, `.user_set_start(ref, epoch)`, `.commands` (every argv received), `.jobs`, `.fail_updates`, `.silent_updates`, `.accounting`, `.down`; it implements `run(argv) -> str` and applies `check_command`.

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/fake_slurm_cli.py` (a tiny real executable for the subprocess test):

```python
"""Stands in for `squeue` when testing SubprocessRunner: echoes its arguments and the two environment variables."""
import json
import os
import sys

argv = sys.argv[1:]
if "boom" in argv:
    print("boom failed", file=sys.stderr)
    sys.exit(3)
print(json.dumps({"argv": argv, "TZ": os.environ.get("TZ"), "fmt": os.environ.get("SLURM_TIME_FORMAT")}))
```

Create `agent/tests/test_runner.py`:

```python
import json
import sys
from pathlib import Path

import pytest

from wattshift_agent.audit import make_audit_logger
from wattshift_agent.runner import ForbiddenCommand, SlurmError, SubprocessRunner, check_command

CLI = Path(__file__).parent / "fake_slurm_cli.py"
DEFER = ["scontrol", "update", "JobId=5", "StartTime=2026-09-20T06:30:00"]


@pytest.mark.parametrize(
    "argv",
    [["squeue", "-h"], ["sacct", "-j", "1"], ["scontrol", "show", "job", "123"], ["scontrol", "update", "JobId=5", "StartTime=now"]],
)
@pytest.mark.parametrize("allow_defer", [True, False])
def test_reads_and_releases_are_allowed_in_both_modes(argv, allow_defer):
    check_command(argv, allow_defer=allow_defer)


def test_deferring_needs_autonomous_mode():
    check_command(DEFER, allow_defer=True)
    with pytest.raises(ForbiddenCommand, match="shadow"):
        check_command(DEFER, allow_defer=False)


@pytest.mark.parametrize(
    "argv",
    [
        ["scancel", "5"], ["scontrol", "hold", "5"], ["scontrol", "shutdown"], ["sbatch", "x"], ["bash", "-c", "x"], [],
        ["scontrol", "update", "JobId=5", "Priority=0"],
        ["scontrol", "update", "JobId=5", "StartTime=now", "Comment=x"],
        ["scontrol", "update", "JobId=5,6", "StartTime=now"],
        ["scontrol", "update", "JobId=5_1", "StartTime=now"],
        ["scontrol", "update", "JobId=5", "StartTime=2026-09-20 06:30:00"],
        ["scontrol", "update", "JobId=5", "StartTime=@1789885800"],
        ["scontrol", "show", "job", "5;rm"], ["scontrol", "show", "node", "c1"],
    ],
)
def test_everything_else_is_refused(argv):
    with pytest.raises(ForbiddenCommand):
        check_command(argv, allow_defer=True)


def runner(**kw):
    return SubprocessRunner(prefix=[sys.executable, str(CLI)], allow_defer=True, **kw)


def test_commands_run_with_utc_and_epoch_times():
    out = json.loads(runner().run(["squeue", "-h"]))
    assert out == {"argv": ["squeue", "-h"], "TZ": "UTC", "fmt": "%s"}


def test_a_failing_command_raises_with_its_message():
    with pytest.raises(SlurmError, match="boom failed"):
        runner().run(["squeue", "boom"])


def test_a_missing_binary_is_a_slurm_error():
    with pytest.raises(SlurmError, match="not found"):
        SubprocessRunner(prefix=["no-such-binary-xyz"], allow_defer=True).run(["squeue"])


def test_a_refused_command_never_starts_a_process(tmp_path):
    marker = tmp_path / "ran"
    r = SubprocessRunner(prefix=[sys.executable, "-c", f"open(r'{marker}', 'w').write('x')"], allow_defer=False)
    with pytest.raises(ForbiddenCommand):
        r.run(DEFER)
    with pytest.raises(ForbiddenCommand):
        r.run(["scancel", "5"])
    assert not marker.exists()


def test_every_command_is_audited(tmp_path):
    log = make_audit_logger(tmp_path / "audit.log")
    r = runner(audit=log)
    r.run(["squeue", "-h"])
    with pytest.raises(SlurmError):
        r.run(["squeue", "boom"])
    for h in log.handlers:
        h.flush()
    lines = (tmp_path / "audit.log").read_text().splitlines()
    assert len(lines) == 2
    assert '"cmd": ["squeue", "-h"]' in lines[0] and '"rc": 0' in lines[0]
    assert '"rc": 3' in lines[1]
```

Create `agent/tests/fakeslurm.py`:

```python
"""An in-process Slurm stand-in. It answers the exact commands the agent sends, in the text formats recorded in
tests/fixtures/slurm (epoch times, pipe-delimited squeue and sacct rows, scontrol key=value blocks), and it applies
the same allow-list as the real runner, so a test also proves the agent only issues allowed commands."""
import calendar
import time

from wattshift_agent.runner import SlurmError, check_command


class FakeSlurm:
    def __init__(self, now: int = 1_790_000_000, allow_defer: bool = True):
        self.now, self.allow_defer = now, allow_defer
        self.jobs: dict[str, dict] = {}
        self.commands: list[list[str]] = []
        self.fail_updates: set[str] = set()  # `scontrol update` on these ids is refused ("Invalid user id")
        self.silent_updates: set[str] = set()  # ...or accepted but changes nothing
        self.accounting = True  # False: sacct knows nothing (accounting has not caught up)
        self.down = False  # True: every command fails, like an unreachable controller

    def add(self, ref, *, state="PENDING", user="alice", account="labs", qos="flex", partition="cpu", name="train",
            gpus=4, limit_min=60, runtime_s=600, predicted=None, dependency=None, restarts=0, nodes=1, gres=None):
        self.jobs[ref] = dict(
            ref=ref, state=state, state_text=state, user=user, account=account, qos=qos, partition=partition, name=name,
            gpus=gpus, limit_min=limit_min, runtime_s=runtime_s, predicted=predicted, dependency=dependency,
            restarts=restarts, nodes=nodes, gres=gres, begin=None, eligible=self.now, submit=self.now, start=None,
            end=None, held=False,
        )
        return self.jobs[ref]

    # --- things a test can do to the "cluster" -------------------------------------------------------------------
    def advance(self, seconds: int) -> None:
        before, self.now = self.now, self.now + seconds
        for j in self.jobs.values():
            if j["state"] == "PENDING" and not j["held"] and not j["dependency"]:
                gate = max(j["begin"] or 0, j["predicted"] or 0)
                if gate <= self.now:
                    j["state"] = j["state_text"] = "RUNNING"
                    j["start"] = max(gate, before)
            if j["state"] == "RUNNING" and j["start"] + j["runtime_s"] <= self.now:
                j["state"] = j["state_text"] = "COMPLETED"
                j["end"] = j["start"] + j["runtime_s"]

    def cancel(self, ref, by=2001):
        j = self.jobs[ref]
        j["state"], j["state_text"], j["end"] = "CANCELLED", f"CANCELLED by {by}", self.now

    def user_set_start(self, ref, epoch):  # the job's owner edits her own job
        self.jobs[ref]["begin"] = self.jobs[ref]["eligible"] = epoch

    # --- the command interface ------------------------------------------------------------------------------------
    def run(self, argv):
        check_command(argv, self.allow_defer)
        self.commands.append(list(argv))
        if self.down:
            raise SlurmError("slurm_load_jobs error: Unable to contact slurm controller")
        if argv[0] == "squeue":
            return self._squeue()
        if argv[0] == "sacct":
            return self._sacct(argv)
        if argv[1] == "show":
            return self._show(argv[3])
        return self._update(argv[2][len("JobId="):], argv[3][len("StartTime="):])

    def _reason(self, j):
        if j["held"]:
            return "JobHeldUser"
        if j["dependency"]:
            return "Dependency"
        if j["begin"] and j["begin"] > self.now:
            return "BeginTime"
        return "None"

    def _shown_start(self, j):
        if j["state"] == "RUNNING":
            return j["start"]
        return max((x for x in (j["begin"], j["predicted"]) if x), default=None)

    def _squeue(self):
        rows = []
        for j in self.jobs.values():
            if j["state"] in ("PENDING", "RUNNING"):
                start = self._shown_start(j)
                rows.append("|".join([
                    j["ref"], j["state"], j["user"], j["account"], j["qos"], j["partition"], str(j["submit"]),
                    str(start) if start else "N/A", self._reason(j), j["name"],
                ]))
        return "\n".join(rows) + ("\n" if rows else "")

    @staticmethod
    def _limit(minutes):
        if minutes is None:
            return "UNLIMITED"
        d, rem = divmod(minutes, 1440)
        h, m = divmod(rem, 60)
        return f"{d}-{h:02d}:{m:02d}:00" if d else f"{h:02d}:{m:02d}:00"

    def _show(self, ref):
        j = self.jobs.get(ref)
        if j is None or j.get("hide_detail"):  # hide_detail: the job vanished between squeue and scontrol
            raise SlurmError("slurm_load_jobs error: Invalid job id specified")
        u = lambda v: "Unknown" if v is None else str(v)  # noqa: E731
        gres = j["gres"] or (f"gres/gpu:{j['gpus']}" if j["gpus"] else None)
        lines = [
            f"JobId={ref} JobName={j['name']}",
            f"   UserId={j['user']}(2001) GroupId={j['user']}(2001) MCS_label=N/A",
            f"   Priority=4294901744 Nice=0 Account={j['account']} QOS={j['qos']}",
            f"   JobState={j['state']} Reason={self._reason(j)} Dependency={j['dependency'] or '(null)'}",
            f"   Requeue=1 Restarts={j['restarts']} BatchFlag=1 Reboot=0 ExitCode=0:0",
            f"   RunTime=00:00:00 TimeLimit={self._limit(j['limit_min'])} TimeMin=N/A",
            f"   SubmitTime={j['submit']} EligibleTime={u(j['eligible'])} AccrueTime={u(j['eligible'])}",
            f"   StartTime={u(self._shown_start(j))} EndTime=Unknown Deadline=N/A",
            f"   Partition={j['partition']} AllocNode:Sid=slurmctld:2210",
            f"   NumNodes={j['nodes']} NumCPUs=1 NumTasks=1 CPUs/Task=1 ReqB:S:C:T=0:0:*:*",
            "   ReqTRES=cpu=1,mem=11815M,node=1,billing=1",
        ]
        if gres:
            lines.append(f"   TresPerNode={gres}")
        lines.append(f"   SubmitLine=sbatch --parsable --gres=gpu:{j['gpus']} -t {j['limit_min']} --wrap=sleep 5")
        return "\n".join(lines) + "\n"

    def _update(self, ref, value):
        j = self.jobs.get(ref)
        if ref in self.fail_updates:
            raise SlurmError(f"Invalid user id for job {ref}")
        if j is None or j["state"] != "PENDING":
            raise SlurmError("Job is no longer pending execution")
        if ref in self.silent_updates:
            return ""
        if value == "now":
            j["begin"], j["eligible"] = None, self.now
        else:
            t = calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%S"))
            j["begin"] = j["eligible"] = t
        return ""

    def _sacct(self, argv):
        if not self.accounting:
            return ""
        rows = []
        for ref in argv[argv.index("-j") + 1].split(","):
            j = self.jobs.get(ref)
            if j is None:
                continue
            elapsed = (j["end"] - j["start"]) if j["start"] and j["end"] else 0
            rows.append("|".join([
                ref, j["state_text"], str(j["start"]) if j["start"] else "None", str(j["end"]) if j["end"] else "Unknown",
                str(elapsed), "billing=1,cpu=1,mem=11815M,node=1",
            ]))
        return "\n".join(rows) + ("\n" if rows else "")
```

Create `agent/tests/test_slurm.py`:

```python
import pytest

from tests.fakeslurm import FakeSlurm
from wattshift_agent.runner import ForbiddenCommand, SlurmError
from wattshift_agent.slurm import Slurm, fmt_utc

NOW = 1_790_000_000  # 2026-09-21T14:13:20Z


def test_fmt_utc():
    assert fmt_utc(NOW) == "2026-09-21T14:13:20"
    assert fmt_utc(1789885800) == "2026-09-20T06:30:00"  # the epoch recorded in Spike 0's time-zone test


def test_queue_and_detail():
    fake = FakeSlurm(NOW)
    fake.add("101", gpus=3, limit_min=90)
    s = Slurm(fake)
    [row] = s.queue()
    assert (row.ref, row.state, row.qos, row.submit) == ("101", "PENDING", "flex", NOW)
    d = s.detail("101")
    assert d.gpus == 3 and d.time_limit_min == 90 and d.state == "PENDING" and d.eligible == NOW


def test_a_job_slurm_no_longer_knows_has_no_detail():
    assert Slurm(FakeSlurm(NOW)).detail("999") is None


def test_set_start_holds_the_job_until_then():
    fake = FakeSlurm(NOW)
    fake.add("101")
    s = Slurm(fake)
    s.set_start("101", NOW + 3600)
    assert ["scontrol", "update", "JobId=101", "StartTime=2026-09-21T15:13:20"] in fake.commands
    d = s.detail("101")
    assert d.eligible == NOW + 3600 and d.reason == "BeginTime"
    fake.advance(1800)
    assert s.detail("101").state == "PENDING"
    fake.advance(1801)
    assert s.detail("101").state == "RUNNING"


def test_release_starts_the_job_now():
    fake = FakeSlurm(NOW)
    fake.add("101")
    s = Slurm(fake)
    s.set_start("101", NOW + 3600)
    s.release("101")
    fake.advance(1)
    assert s.detail("101").state == "RUNNING"


def test_finished_jobs_come_from_accounting_not_the_queue():
    fake = FakeSlurm(NOW)
    fake.add("101", runtime_s=60)
    s = Slurm(fake)
    fake.advance(1)
    fake.advance(120)
    assert s.queue() == []
    [a] = s.finished(["101"])
    assert (a.ref, a.state, a.end - a.start) == ("101", "COMPLETED", 60)
    assert s.finished([]) == []


def test_updating_a_job_that_already_started_is_an_error():
    fake = FakeSlurm(NOW)
    fake.add("101")
    fake.advance(1)
    with pytest.raises(SlurmError, match="no longer pending"):
        Slurm(fake).set_start("101", NOW + 3600)


def test_the_shadow_fake_refuses_a_deferral_but_allows_a_release():
    fake = FakeSlurm(NOW, allow_defer=False)
    fake.add("101")
    s = Slurm(fake)
    with pytest.raises(ForbiddenCommand):
        s.set_start("101", NOW + 3600)
    s.release("101")
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_runner.py tests/test_slurm.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'wattshift_agent.audit'`.

- [ ] **Step 3: Write `audit.py`, `runner.py`, `slurm.py`**

Create `agent/wattshift_agent/audit.py`:

```python
"""A rotating JSON-lines audit log: every Slurm command the agent runs and every decision it acts on."""
import json
import logging
import time
from logging.handlers import RotatingFileHandler


def make_audit_logger(path) -> logging.Logger:
    log = logging.getLogger(f"wattshift.audit.{path}")
    if not log.handlers:
        handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fmt = logging.Formatter("%(asctime)sZ %(message)s", "%Y-%m-%dT%H:%M:%S")
        fmt.converter = time.gmtime
        handler.setFormatter(fmt)
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def event(log, name: str, **fields) -> None:
    """One audit line; a no-op when there is no audit logger (unit tests)."""
    if log is not None:
        log.info(json.dumps({"event": name, **fields}, default=str))
```

Create `agent/wattshift_agent/runner.py`:

```python
"""The only place the agent touches Slurm: an allow-list of commands, a fixed environment, one audit line each."""
import json
import os
import re
import subprocess
import time

JOB_ID = re.compile(r"\d+")
ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class ForbiddenCommand(Exception):
    """The agent tried to run something that is not on its allow-list. Always a bug; nothing was executed."""


class SlurmError(Exception):
    """A Slurm command failed (non-zero exit, timeout, or the binary is missing)."""


def check_command(argv, allow_defer: bool) -> None:
    """Raise ForbiddenCommand unless argv is one of the few commands the agent may run.
    Reads: squeue, sacct, `scontrol show job <id>`. Writes: only `scontrol update JobId=<id> StartTime=<value>`, where
    `now` (a release) is always allowed, so a shadow agent can undo its own earlier deferrals, and an absolute UTC time
    (a deferral) only when allow_defer."""
    if not argv:
        raise ForbiddenCommand("empty command")
    cmd, rest = argv[0], argv[1:]
    if cmd in ("squeue", "sacct"):
        return
    if cmd == "scontrol" and len(rest) == 3 and rest[:2] == ["show", "job"] and JOB_ID.fullmatch(rest[2]):
        return
    if (
        cmd == "scontrol" and len(rest) == 3 and rest[0] == "update" and rest[1].startswith("JobId=")
        and JOB_ID.fullmatch(rest[1][len("JobId="):]) and rest[2].startswith("StartTime=")
    ):
        value = rest[2][len("StartTime="):]
        if value == "now":
            return
        if ISO_UTC.fullmatch(value):
            if allow_defer:
                return
            raise ForbiddenCommand("setting a future start time is not allowed in shadow mode")
    raise ForbiddenCommand(f"command not on the allow-list: {' '.join(argv)[:80]}")


class SubprocessRunner:
    def __init__(self, *, allow_defer: bool, prefix=(), audit=None, timeout: int = 30):
        self.allow_defer, self.prefix, self.audit, self.timeout = allow_defer, list(prefix), audit, timeout

    def run(self, argv) -> str:
        check_command(argv, self.allow_defer)
        env = {**os.environ, "TZ": "UTC", "SLURM_TIME_FORMAT": "%s"}  # UTC in, epoch seconds out (Spike 0)
        t0 = time.monotonic()
        try:
            p = subprocess.run([*self.prefix, *argv], capture_output=True, text=True, timeout=self.timeout, env=env)
        except FileNotFoundError:
            self._log(argv, None, t0)
            raise SlurmError(f"{(self.prefix or argv)[0]} not found on this machine")
        except subprocess.TimeoutExpired:
            self._log(argv, "timeout", t0)
            raise SlurmError(f"{argv[0]} timed out after {self.timeout} s")
        self._log(argv, p.returncode, t0)
        if p.returncode != 0:
            raise SlurmError((p.stderr or p.stdout).strip()[:300] or f"{argv[0]} exited with {p.returncode}")
        return p.stdout

    def _log(self, argv, rc, t0) -> None:
        if self.audit is not None:
            self.audit.info(json.dumps({"cmd": list(argv), "rc": rc, "ms": int((time.monotonic() - t0) * 1000)}))
```

Create `agent/wattshift_agent/slurm.py`:

```python
"""What the agent asks Slurm, in one place. Each method is one allow-listed command plus its parser."""
from datetime import datetime, timezone

from wattshift_agent.parse import AcctRow, JobDetail, ParseError, QueueRow, parse_acct, parse_detail, parse_queue
from wattshift_agent.runner import SlurmError

QUEUE_FORMAT = "%i|%T|%u|%a|%q|%P|%V|%S|%r|%j"
ACCT_FIELDS = "JobID,State,Start,End,ElapsedRaw,AllocTRES"


def fmt_utc(epoch: int) -> str:
    """The absolute-time form scontrol accepts, in UTC (the runner sets TZ=UTC so Slurm reads it as UTC)."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class Slurm:
    def __init__(self, runner):
        self.runner = runner

    def queue(self) -> list[QueueRow]:
        return parse_queue(self.runner.run(["squeue", "-h", "-t", "PENDING,RUNNING", "-o", QUEUE_FORMAT]))

    def detail(self, ref: str) -> JobDetail | None:
        """None when Slurm no longer knows the job (it finished and aged out, or was cancelled)."""
        try:
            return parse_detail(self.runner.run(["scontrol", "show", "job", ref]), ref)
        except SlurmError as e:
            if "Invalid job id" in str(e):
                return None
            raise
        except ParseError:
            return None

    def finished(self, refs: list[str]) -> list[AcctRow]:
        if not refs:
            return []
        out = self.runner.run(["sacct", "-j", ",".join(refs), "-P", "-X", "-n", "-S", "now-14days", "-o", ACCT_FIELDS])
        return parse_acct(out)

    def set_start(self, ref: str, epoch: int) -> None:
        self.runner.run(["scontrol", "update", f"JobId={ref}", f"StartTime={fmt_utc(epoch)}"])

    def release(self, ref: str) -> None:
        self.runner.run(["scontrol", "update", f"JobId={ref}", "StartTime=now"])
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_runner.py tests/test_slurm.py -q`
Expected: all pass.

- [ ] **Step 5: Mutation-check the allow-list**

Temporarily change `if allow_defer:` in `check_command` to `if True:` and confirm `test_deferring_needs_autonomous_mode` and `test_a_refused_command_never_starts_a_process` fail; then remove `rest[2].startswith("StartTime=")` from the update condition and confirm `test_everything_else_is_refused[...Priority=0]` fails. Restore both.

---

### Task 5: Local state

**Files:**
- Create: `agent/wattshift_agent/state.py`, `agent/tests/test_state.py`

**Interfaces:**
- Produces (in `wattshift_agent.state`):
  - `Tracked` dataclass: `ref, first_seen, max_wait_min, state` (required) and `submit=None, gpus=None, time_limit_min=None, predicted_start=None, skipped_reason=None, applied_start=None, released=False, override=False, start_time=None, end_time=None, final_sent=False`
  - `State(path)` with `get(ref) -> Tracked | None`, `save(t)`, `active() -> list[Tracked]` (rows whose final report has not been sent, ordered by ref), `mark_sent(refs)`, `put_applied(ref, start_at, ok, error)`, `applied_outbox() -> list[tuple[ref, start_at, ok, error]]`, `clear_applied(refs)`, `add_released(ref)`, `released_outbox() -> list[str]`, `clear_released(refs)`, `get_meta(key, default=None)`, `set_meta(key, value)`, `prune(older_than: int)`

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_state.py`:

```python
from wattshift_agent.state import State, Tracked


def tracked(ref="1", **kw):
    return Tracked(ref=ref, first_seen=100, max_wait_min=1440, state="PENDING", **kw)


def test_a_job_round_trips_including_flags(tmp_path):
    s = State(tmp_path / "s.db")
    s.save(tracked("1", gpus=8, time_limit_min=90, submit=50, predicted_start=200, skipped_reason="array",
                   applied_start=300, released=True, override=True, start_time=310, end_time=400, final_sent=True))
    t = s.get("1")
    assert (t.gpus, t.time_limit_min, t.submit, t.predicted_start, t.skipped_reason) == (8, 90, 50, 200, "array")
    assert (t.applied_start, t.released, t.override, t.start_time, t.end_time, t.final_sent) == (300, True, True, 310, 400, True)
    assert s.get("nope") is None


def test_save_replaces_and_defaults_are_off(tmp_path):
    s = State(tmp_path / "s.db")
    s.save(tracked("1"))
    t = s.get("1")
    assert (t.released, t.override, t.final_sent, t.applied_start, t.gpus) == (False, False, False, None, None)
    t.state = "RUNNING"
    s.save(t)
    assert s.get("1").state == "RUNNING" and len(s.active()) == 1


def test_active_excludes_jobs_whose_final_report_was_sent(tmp_path):
    s = State(tmp_path / "s.db")
    for ref in ("2", "1", "3"):
        s.save(tracked(ref))
    s.mark_sent(["2"])
    assert [t.ref for t in s.active()] == ["1", "3"]
    s.mark_sent([])  # empty is fine


def test_the_applied_outbox_keeps_reports_until_cleared(tmp_path):
    s = State(tmp_path / "s.db")
    s.put_applied("1", 500, True, None)
    s.put_applied("2", 600, False, "Invalid user id")
    s.put_applied("1", 700, True, None)  # a newer report for the same job replaces the older one
    assert s.applied_outbox() == [("1", 700, True, None), ("2", 600, False, "Invalid user id")]
    s.clear_applied(["1"])
    assert s.applied_outbox() == [("2", 600, False, "Invalid user id")]


def test_the_released_outbox_and_meta(tmp_path):
    s = State(tmp_path / "s.db")
    s.add_released("1")
    s.add_released("1")
    s.add_released("2")
    assert s.released_outbox() == ["1", "2"]
    s.clear_released(["1"])
    assert s.released_outbox() == ["2"]
    assert s.get_meta("release_requested") is None and s.get_meta("release_requested", "0") == "0"
    s.set_meta("release_requested", "1")
    assert s.get_meta("release_requested") == "1"


def test_everything_survives_a_restart(tmp_path):
    s = State(tmp_path / "s.db")
    s.save(tracked("1", applied_start=300))
    s.put_applied("1", 300, True, None)
    s.set_meta("k", "v")
    s.db.close()
    again = State(tmp_path / "s.db")
    assert again.get("1").applied_start == 300 and again.applied_outbox() == [("1", 300, True, None)]
    assert again.get_meta("k") == "v"


def test_prune_drops_only_old_finished_rows(tmp_path):
    s = State(tmp_path / "s.db")
    s.save(tracked("old", end_time=1_000, final_sent=True))
    s.save(tracked("new", end_time=9_000, final_sent=True))
    s.save(tracked("live"))
    s.prune(older_than=5_000)
    assert s.get("old") is None and s.get("new") is not None and s.get("live") is not None
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_state.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'wattshift_agent.state'`.

- [ ] **Step 3: Write `state.py`**

Create `agent/wattshift_agent/state.py`:

```python
"""The agent's local memory (SQLite): what it has seen and applied. Needed to detect a user's own change to a job and
to retry reports the cloud has not acknowledged. Losing the file is safe: jobs still start when Slurm says."""
import sqlite3
from dataclasses import asdict, dataclass, fields

SCHEMA = """
create table if not exists jobs (
  ref text primary key, first_seen integer not null, max_wait_min integer not null, state text not null,
  submit integer, gpus integer, time_limit_min integer, predicted_start integer, skipped_reason text,
  applied_start integer, released integer not null default 0, override integer not null default 0,
  start_time integer, end_time integer, final_sent integer not null default 0
);
create table if not exists outbox_applied (ref text primary key, start_at integer not null, ok integer not null, error text);
create table if not exists outbox_released (ref text primary key);
create table if not exists meta (key text primary key, value text);
"""
_BOOLS = ("released", "override", "final_sent")


@dataclass
class Tracked:
    ref: str
    first_seen: int
    max_wait_min: int
    state: str  # PENDING | RUNNING | COMPLETED | FAILED | CANCELLED | TIMEOUT | OTHER
    submit: int | None = None
    gpus: int | None = None  # captured at first sight; sacct may not have it later
    time_limit_min: int | None = None
    predicted_start: int | None = None
    skipped_reason: str | None = None  # array | dependency | requeue: reported, never planned
    applied_start: int | None = None  # the start time WE set (the basis for detecting a user's change)
    released: bool = False
    override: bool = False  # the owner changed, held or cancelled it after we deferred it
    start_time: int | None = None
    end_time: int | None = None
    final_sent: bool = False


_COLS = [f.name for f in fields(Tracked)]


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    @staticmethod
    def _tracked(row) -> Tracked:
        d = dict(row)
        for b in _BOOLS:
            d[b] = bool(d[b])
        return Tracked(**d)

    def get(self, ref: str) -> Tracked | None:
        row = self.db.execute("select * from jobs where ref = ?", (ref,)).fetchone()
        return self._tracked(row) if row else None

    def save(self, t: Tracked) -> None:
        d = asdict(t)
        vals = [int(d[c]) if isinstance(d[c], bool) else d[c] for c in _COLS]
        self.db.execute(f"insert or replace into jobs ({','.join(_COLS)}) values ({','.join('?' * len(_COLS))})", vals)
        self.db.commit()

    def active(self) -> list[Tracked]:
        return [self._tracked(r) for r in self.db.execute("select * from jobs where final_sent = 0 order by ref")]

    def mark_sent(self, refs) -> None:
        self.db.executemany("update jobs set final_sent = 1 where ref = ?", [(r,) for r in refs])
        self.db.commit()

    def put_applied(self, ref: str, start_at: int, ok: bool, error: str | None) -> None:
        self.db.execute("insert or replace into outbox_applied values (?, ?, ?, ?)", (ref, start_at, int(ok), error))
        self.db.commit()

    def applied_outbox(self) -> list[tuple]:
        return [(r["ref"], r["start_at"], bool(r["ok"]), r["error"]) for r in self.db.execute("select * from outbox_applied order by ref")]

    def clear_applied(self, refs) -> None:
        self.db.executemany("delete from outbox_applied where ref = ?", [(r,) for r in refs])
        self.db.commit()

    def add_released(self, ref: str) -> None:
        self.db.execute("insert or ignore into outbox_released values (?)", (ref,))
        self.db.commit()

    def released_outbox(self) -> list[str]:
        return [r["ref"] for r in self.db.execute("select ref from outbox_released order by ref")]

    def clear_released(self, refs) -> None:
        self.db.executemany("delete from outbox_released where ref = ?", [(r,) for r in refs])
        self.db.commit()

    def get_meta(self, key: str, default=None):
        row = self.db.execute("select value from meta where key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("insert or replace into meta values (?, ?)", (key, value))
        self.db.commit()

    def prune(self, older_than: int) -> None:
        """Forget jobs whose final report was sent before `older_than` (epoch seconds)."""
        self.db.execute("delete from jobs where final_sent = 1 and coalesce(end_time, first_seen) < ?", (older_than,))
        self.db.commit()
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_state.py -q`
Expected: all pass.

---

### Task 6: Observing Slurm (discover, refresh, build the request body)

**Files:**
- Create: `agent/wattshift_agent/cycle.py` (first half), `agent/tests/conftest.py`, `agent/tests/test_observe.py`

**Interfaces:**
- Consumes: `Config`, `Rule`, `max_wait_for` (Task 2), `parse` (Task 3), `Slurm` (Task 4), `State`, `Tracked` (Task 5), `audit.event`.
- Produces (in `wattshift_agent.cycle`):
  - constants `MAX_NEW_PER_CYCLE = 200`, `MAX_JOBS_PER_SYNC = 2000`, `GIVE_UP_AFTER_S = 86_400`
  - `iso(ts) -> str | None` (`2026-09-21T14:13:20Z`)
  - `discover(cfg, slurm, state, rows, now, audit=None) -> int` (new jobs tracked)
  - `refresh(slurm, state, by_ref, now, audit=None) -> None`
  - `observe(cfg, slurm, state, now, audit=None) -> None` (one `squeue`, then `discover`, then `refresh`)
  - `Outgoing(payload, applied, released, final, release_requested)` and `build_body(cfg, state, now) -> Outgoing`

- [ ] **Step 1: Shared fixtures**

Create `agent/tests/conftest.py`:

```python
import pytest

from tests.fakecloud import FakeCloud
from tests.fakeslurm import FakeSlurm
from wattshift_agent.config import Config, Rule
from wattshift_agent.slurm import Slurm
from wattshift_agent.state import State

NOW = 1_790_000_000  # 2026-09-21T14:13:20Z


def make_cfg(tmp_path, mode="autonomous"):
    return Config(
        cloud_url="http://cloud.test", site_key="wsk_test", mode=mode, rules=(Rule((("qos", "flex"),), None, 1440),),
        state_path=tmp_path / "state.db", audit_path=tmp_path / "audit.log",
    )


@pytest.fixture()
def cfg(tmp_path):
    return make_cfg(tmp_path)


@pytest.fixture()
def fake():
    return FakeSlurm(NOW)


@pytest.fixture()
def slurm(fake):
    return Slurm(fake)


@pytest.fixture()
def state(tmp_path):
    return State(tmp_path / "state.db")


@pytest.fixture()
def cloud():
    return FakeCloud()
```

Create `agent/tests/fakecloud.py` (used from Task 8; created now so `conftest.py` imports work):

```python
"""An in-process cloud: records every request body and answers from a queue of canned replies."""
from wattshift_agent.client import CloudError


class FakeCloud:
    def __init__(self):
        self.bodies: list[dict] = []
        self.replies: list[dict] = []
        self.fail = False

    def reply(self, **fields) -> None:
        self.replies.append(fields)

    def post_sync(self, body: dict) -> dict:
        if self.fail:
            raise CloudError("connection refused")
        self.bodies.append(body)
        extra = self.replies.pop(0) if self.replies else {}
        return {"decisions": [], "release_all": False, "next_poll_s": 30, **extra}
```

`fakecloud.py` imports `wattshift_agent.client`, which Task 8 creates. To let Task 6 run first, create a stub now: `agent/wattshift_agent/client.py` containing only

```python
class CloudError(Exception):
    """The cloud could not be reached, or answered with an error."""
```

(Task 8 replaces the file with the full client, keeping this class.)

- [ ] **Step 2: Write the failing tests**

Create `agent/tests/test_observe.py`:

```python
import pytest

from tests.conftest import NOW
from wattshift_agent import cycle
from wattshift_agent.cycle import build_body, iso, observe


def shows(fake, ref):
    return ["scontrol", "show", "job", ref] in fake.commands


def test_iso():
    assert iso(NOW) == "2026-09-21T14:13:20Z" and iso(None) is None


def test_only_jobs_matching_a_rule_are_ever_read(cfg, fake, slurm, state):
    fake.add("1", qos="normal")
    fake.add("2", qos="flex")
    observe(cfg, slurm, state, NOW)
    assert state.get("1") is None and state.get("2") is not None
    assert not shows(fake, "1") and shows(fake, "2")


def test_first_sight_facts_are_recorded_once(cfg, fake, slurm, state):
    fake.add("101", gpus=3, limit_min=90)
    observe(cfg, slurm, state, NOW)
    t = state.get("101")
    assert (t.first_seen, t.gpus, t.time_limit_min, t.max_wait_min, t.state, t.submit) == (NOW, 3, 90, 1440, "PENDING", NOW)
    observe(cfg, slurm, state, NOW + 30)
    assert fake.commands.count(["scontrol", "show", "job", "101"]) == 1  # detail is read once, not every poll
    assert state.get("101").first_seen == NOW


def test_a_job_already_running_at_first_sight_is_ignored(cfg, fake, slurm, state):
    fake.add("101", state="RUNNING")
    fake.jobs["101"]["start"] = NOW - 10
    observe(cfg, slurm, state, NOW)
    assert state.get("101") is None


@pytest.mark.parametrize(
    "ref, extra, reason",
    [("41_[1-3]", {}, "array"), ("42", {"dependency": "afterok:40(unfulfilled)"}, "dependency"), ("43", {"restarts": 1}, "requeue")],
)
def test_jobs_v1_leaves_alone_are_recorded_with_a_reason(cfg, fake, slurm, state, ref, extra, reason):
    fake.add(ref, **extra)
    observe(cfg, slurm, state, NOW)
    assert state.get(ref).skipped_reason == reason
    if reason == "array":
        assert not shows(fake, ref)  # arrays are recognised from the id, no detail call


def test_a_job_that_vanishes_between_the_two_calls_is_not_tracked(cfg, fake, slurm, state):
    fake.add("777")
    fake.jobs["777"]["hide_detail"] = True  # squeue lists it, then scontrol says "Invalid job id"
    observe(cfg, slurm, state, NOW)
    assert state.get("777") is None


def test_at_most_a_few_new_jobs_are_read_per_cycle(cfg, fake, slurm, state, monkeypatch):
    monkeypatch.setattr(cycle, "MAX_NEW_PER_CYCLE", 2)
    for i in range(5):
        fake.add(str(100 + i))
    for expected in (2, 4, 5):
        observe(cfg, slurm, state, NOW)
        assert len(state.active()) == expected


def test_predicted_start_is_recorded_and_guarded(cfg, fake, slurm, state):
    fake.add("101", predicted=NOW + 7200)
    fake.add("102", predicted=NOW + 365 * 86400)  # Slurm's "exactly one year" answer when running jobs have no limit
    observe(cfg, slurm, state, NOW)
    facts = {f["ref"]: f for f in build_body(cfg, state, NOW).payload["jobs"]}
    assert facts["101"]["predicted_start"] == iso(NOW + 7200)
    assert facts["102"]["predicted_start"] is None  # beyond first_seen + max_wait: dropped from the request


def test_a_late_prediction_is_picked_up_until_we_defer_the_job(cfg, fake, slurm, state):
    fake.add("101")
    observe(cfg, slurm, state, NOW)
    assert state.get("101").predicted_start is None  # N/A for the first ~30 s
    fake.jobs["101"]["predicted"] = NOW + 600
    observe(cfg, slurm, state, NOW + 30)
    assert state.get("101").predicted_start == NOW + 600
    slurm.set_start("101", NOW + 3600)  # we defer it: the baseline freezes
    t = state.get("101")
    t.applied_start = NOW + 3600
    state.save(t)
    fake.jobs["101"]["predicted"] = NOW + 9000
    observe(cfg, slurm, state, NOW + 60)
    assert state.get("101").predicted_start == NOW + 600


def test_running_then_finished_is_read_from_accounting(cfg, fake, slurm, state):
    fake.add("101", runtime_s=60)
    observe(cfg, slurm, state, NOW)
    fake.advance(1)
    observe(cfg, slurm, state, NOW + 1)
    t = state.get("101")
    assert t.state == "RUNNING" and t.start_time == NOW
    fake.advance(120)
    observe(cfg, slurm, state, NOW + 121)
    t = state.get("101")
    assert (t.state, t.start_time, t.end_time) == ("COMPLETED", NOW, NOW + 60)
    assert ["sacct", "-j", "101"] == [c for c in fake.commands if c[0] == "sacct"][0][:3]


def test_a_job_cancelled_while_pending_has_no_start(cfg, fake, slurm, state):
    fake.add("101")
    observe(cfg, slurm, state, NOW)
    fake.cancel("101")
    observe(cfg, slurm, state, NOW + 10)
    t = state.get("101")
    assert t.state == "CANCELLED" and t.start_time is None and t.end_time == NOW


def test_accounting_that_never_catches_up_is_given_up_after_a_day(cfg, fake, slurm, state):
    fake.accounting = False
    fake.add("101", runtime_s=60)
    observe(cfg, slurm, state, NOW)
    fake.advance(1)
    observe(cfg, slurm, state, NOW + 1)  # we see it running...
    fake.advance(120)  # ...then it finishes, but accounting knows nothing yet
    observe(cfg, slurm, state, NOW + 121)
    assert state.get("101").state == "RUNNING"  # unchanged: no evidence yet, so ask again next cycle
    observe(cfg, slurm, state, NOW + 86_401)
    assert state.get("101").state == "OTHER"


def test_requeue_after_start_stops_management(cfg, fake, slurm, state):
    fake.add("101")
    observe(cfg, slurm, state, NOW)
    fake.advance(1)
    observe(cfg, slurm, state, NOW + 1)
    fake.jobs["101"].update(state="PENDING", state_text="PENDING", restarts=1, start=None, begin=NOW + 900)
    observe(cfg, slurm, state, NOW + 5)
    t = state.get("101")
    assert t.state == "PENDING" and t.skipped_reason == "requeue"


def deferred(cfg, fake, slurm, state, when=NOW + 3600):
    fake.add("101")
    observe(cfg, slurm, state, NOW)
    slurm.set_start("101", when)
    t = state.get("101")
    t.applied_start = when
    state.save(t)


def test_our_own_deferral_is_not_an_override_even_if_slurm_predicts_later(cfg, fake, slurm, state):
    deferred(cfg, fake, slurm, state)
    fake.jobs["101"]["predicted"] = NOW + 9000  # cluster busy: the shown start moves, the begin time does not
    observe(cfg, slurm, state, NOW + 30)
    assert state.get("101").override is False


def test_the_owner_changing_the_start_time_is_an_override(cfg, fake, slurm, state):
    deferred(cfg, fake, slurm, state)
    fake.user_set_start("101", NOW + 60)
    observe(cfg, slurm, state, NOW + 30)
    assert state.get("101").override is True


def test_the_owner_holding_the_job_is_an_override(cfg, fake, slurm, state):
    deferred(cfg, fake, slurm, state)
    fake.jobs["101"]["held"] = True
    observe(cfg, slurm, state, NOW + 30)
    assert state.get("101").override is True


def test_the_body_lists_facts_and_nothing_private(cfg, fake, slurm, state):
    fake.add("101", user="alice", account="secret-lab", name="private-project-x", gpus=2, limit_min=30)
    observe(cfg, slurm, state, NOW)
    out = build_body(cfg, state, NOW)
    assert out.payload["jobs"] == [{
        "ref": "101", "state": "PENDING", "submit_time": iso(NOW), "gpus": 2, "time_limit_min": 30, "max_wait_min": 1440,
        "predicted_start": None, "start_time": None, "end_time": None, "override": False, "skipped_reason": None,
    }]
    text = str(out.payload)
    assert "alice" not in text and "secret-lab" not in text and "private-project-x" not in text
    assert out.payload["mode"] == "autonomous" and out.payload["sent_at"] == iso(NOW)
    assert out.payload["applied"] == [] and out.payload["released"] == [] and out.payload["release_all"] is False


def test_the_body_carries_the_outboxes_and_the_release_request(cfg, fake, slurm, state):
    fake.add("101")
    observe(cfg, slurm, state, NOW)
    state.put_applied("101", NOW + 3600, True, None)
    state.put_applied("102", NOW + 7200, False, "Invalid user id for job 102")
    state.add_released("103")
    state.set_meta("release_requested", "1")
    out = build_body(cfg, state, NOW)
    assert out.payload["applied"] == [
        {"ref": "101", "start_at": iso(NOW + 3600), "ok": True, "error": None},
        {"ref": "102", "start_at": iso(NOW + 7200), "ok": False, "error": "Invalid user id for job 102"},
    ]
    assert out.payload["released"] == ["103"] and out.payload["release_all"] is True
    assert (out.applied, out.released, out.release_requested) == (["101", "102"], ["103"], True)


def test_finished_jobs_are_reported_until_the_report_is_acknowledged(cfg, fake, slurm, state):
    fake.add("101", runtime_s=10)
    observe(cfg, slurm, state, NOW)
    fake.advance(1)
    fake.advance(60)
    observe(cfg, slurm, state, NOW + 61)
    out = build_body(cfg, state, NOW + 61)
    assert out.final == ["101"] and out.payload["jobs"][0]["state"] == "COMPLETED"
    assert build_body(cfg, state, NOW + 62).final == ["101"]  # still there: nothing marked it sent
    state.mark_sent(out.final)
    assert build_body(cfg, state, NOW + 63).payload["jobs"] == []
```

- [ ] **Step 3: Run them and confirm they fail**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_observe.py -q`
Expected: FAIL, `ImportError: cannot import name 'cycle'`.

- [ ] **Step 4: Write the first half of `cycle.py`**

Create `agent/wattshift_agent/cycle.py`:

```python
"""One agent cycle: read Slurm, keep the local record, tell the cloud, apply what it answers (spec sections 4 and 6)."""
from dataclasses import dataclass
from datetime import datetime, timezone

from wattshift_agent import __version__
from wattshift_agent.audit import event
from wattshift_agent.parse import TERMINAL
from wattshift_agent.rules import max_wait_for
from wattshift_agent.state import Tracked

MAX_NEW_PER_CYCLE = 200  # ponytail: bounds the scontrol calls after a restart with a huge backlog; the rest follow next cycle
MAX_JOBS_PER_SYNC = 2000  # the cloud's request limit
GIVE_UP_AFTER_S = 86_400  # a job that left the queue and never shows in accounting is closed as OTHER


def iso(ts: int | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def discover(cfg, slurm, state, rows, now: int, audit=None) -> int:
    """Start tracking pending jobs that match a flex rule. A job matching no rule is never recorded, read further or
    sent. Returns how many jobs were added."""
    added = 0
    for r in rows:
        if r.state != "PENDING" or state.get(r.ref) is not None:
            continue
        wait = max_wait_for(cfg.rules, r)
        if wait is None:
            continue
        if added >= MAX_NEW_PER_CYCLE:
            break
        t = Tracked(ref=r.ref, first_seen=now, max_wait_min=wait, state="PENDING", submit=r.submit, predicted_start=r.start)
        if "_" in r.ref:  # array job (41_3, 41_[1-3]): left alone in v1
            t.skipped_reason = "array"
        else:
            d = slurm.detail(r.ref)
            if d is None:
                continue  # gone between the two calls: nothing to track
            t.gpus, t.time_limit_min = d.gpus, d.time_limit_min
            t.skipped_reason = "dependency" if d.dependency else "requeue" if d.restarts > 0 else None
        state.save(t)
        added += 1
        event(audit, "job_seen", ref=r.ref, max_wait_min=wait, gpus=t.gpus, skipped=t.skipped_reason)
    return added


def _check_override(slurm, t: Tracked, audit) -> None:
    """A job we deferred is compared with what we set. EligibleTime (not StartTime, which also moves with Slurm's own
    estimate) is the begin time; a different one, or any hold, means the owner changed it."""
    d = slurm.detail(t.ref)
    if d is None:
        return  # it left the queue between the calls; the next cycle closes it
    if d.eligible != t.applied_start or d.reason.startswith("JobHeld"):
        t.override = True
        event(audit, "override_detected", ref=t.ref, expected=t.applied_start, eligible=d.eligible, reason=d.reason)


def _close_finished(slurm, state, gone: list[Tracked], now: int, audit) -> None:
    if not gone:
        return
    rows = {a.ref: a for a in slurm.finished([t.ref for t in gone])}
    for t in gone:
        a = rows.get(t.ref)
        if a is not None and a.state in TERMINAL:
            t.state, t.start_time, t.end_time = a.state, a.start, a.end
            if t.gpus is None and a.gpus is not None:
                t.gpus = a.gpus
        elif a is not None and a.state == "RUNNING":
            t.state, t.start_time = "RUNNING", a.start or t.start_time  # still finishing up
        elif now - t.first_seen > GIVE_UP_AFTER_S:
            t.state = "OTHER"
        else:
            continue  # accounting has not caught up: ask again next cycle
        state.save(t)
        event(audit, "job_finished", ref=t.ref, state=t.state)


def refresh(slurm, state, by_ref: dict, now: int, audit=None) -> None:
    """Bring every tracked job up to date: started, finished (from accounting), or changed by its owner."""
    gone = []
    for t in state.active():
        r = by_ref.get(t.ref)
        if r is None:
            gone.append(t)
            continue
        if r.state == "RUNNING":
            t.state, t.start_time = "RUNNING", r.start or t.start_time
        elif r.state == "PENDING":
            if t.state == "RUNNING":  # started, then put back in the queue: a requeue, not ours to manage
                t.state, t.skipped_reason = "PENDING", t.skipped_reason or "requeue"
            elif t.applied_start is None and not t.released and t.skipped_reason is None:
                t.predicted_start = r.start  # until we defer it, Slurm's own estimate is the baseline
            elif t.applied_start is not None and not t.released and not t.override:
                _check_override(slurm, t, audit)
        state.save(t)
    _close_finished(slurm, state, gone, now, audit)


def observe(cfg, slurm, state, now: int, audit=None) -> None:
    """Everything the agent learns from Slurm in one cycle. Raises SlurmError if Slurm cannot be read."""
    rows = slurm.queue()
    discover(cfg, slurm, state, rows, now, audit)
    refresh(slurm, state, {r.ref: r for r in rows}, now, audit)


@dataclass
class Outgoing:
    payload: dict  # the request body
    applied: list  # refs of the `applied` reports in it (cleared once the cloud accepts them)
    released: list
    final: list  # refs of finished jobs in it (marked sent once the cloud accepts them)
    release_requested: bool


def _fact(t: Tracked) -> dict:
    predicted = t.predicted_start
    if predicted is not None and predicted > t.first_seen + t.max_wait_min * 60:
        predicted = None  # implausible (Slurm's "one year ahead" answer): never sent
    return {
        "ref": t.ref, "state": t.state, "submit_time": iso(t.submit), "gpus": t.gpus, "time_limit_min": t.time_limit_min,
        "max_wait_min": t.max_wait_min, "predicted_start": iso(predicted), "start_time": iso(t.start_time),
        "end_time": iso(t.end_time), "override": t.override, "skipped_reason": t.skipped_reason,
    }


def build_body(cfg, state, now: int) -> Outgoing:
    active = state.active()[:MAX_JOBS_PER_SYNC]
    applied, released = state.applied_outbox(), state.released_outbox()
    payload = {
        "agent_version": __version__, "mode": cfg.mode, "sent_at": iso(now), "jobs": [_fact(t) for t in active],
        "applied": [{"ref": r, "start_at": iso(s), "ok": ok, "error": err} for r, s, ok, err in applied],
        "released": released, "release_all": state.get_meta("release_requested") == "1",
    }
    return Outgoing(payload, [a[0] for a in applied], list(released), [t.ref for t in active if t.state in TERMINAL], payload["release_all"])
```

- [ ] **Step 5: Run them and confirm they pass**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_observe.py -q`
Expected: all pass.

- [ ] **Step 6: Mutation-check override detection**

Temporarily change `d.eligible != t.applied_start` to `d.start != t.applied_start` and confirm `test_our_own_deferral_is_not_an_override_even_if_slurm_predicts_later` FAILS; restore.

---

### Task 7: The applier

**Files:**
- Create: `agent/wattshift_agent/applier.py`, `agent/tests/test_applier.py`

**Interfaces:**
- Consumes: `Slurm`, `SlurmError` (Task 4), `State`, `Tracked` (Task 5), `audit.event`.
- Produces (in `wattshift_agent.applier`):
  - `apply_decision(cfg, slurm, state, ref, start_at, now, audit=None) -> bool`: applies one cloud decision and queues an `applied` report (ok or failed with a reason) in the state's outbox. Never raises for a refusal or a `SlurmError`.
  - `release_all(slurm, state, audit=None) -> int`: sets every job the agent deferred (pending, applied, not yet released) back to "start now", marks it released, queues it in the released outbox, returns the count. Allowed in every mode.

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_applier.py`:

```python
import pytest

from tests.conftest import NOW, make_cfg
from tests.fakeslurm import FakeSlurm
from wattshift_agent.applier import apply_decision, release_all
from wattshift_agent.slurm import Slurm
from wattshift_agent.state import Tracked


def track(state, fake, ref="101", **kw):
    fake.add(ref)
    t = Tracked(ref=ref, first_seen=NOW, max_wait_min=1440, state="PENDING", gpus=4, time_limit_min=60, **kw)
    state.save(t)
    return t


def updates(fake):
    return [c for c in fake.commands if c[:2] == ["scontrol", "update"]]


def test_a_decision_sets_the_start_time_in_utc_and_is_confirmed(cfg, fake, slurm, state):
    track(state, fake)
    assert apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW) is True
    assert updates(fake) == [["scontrol", "update", "JobId=101", "StartTime=2026-09-21T15:13:20"]]
    assert state.get("101").applied_start == NOW + 3600
    assert state.applied_outbox() == [("101", NOW + 3600, True, None)]


def test_a_start_time_slurm_did_not_keep_is_reported_as_a_failure(cfg, fake, slurm, state):
    track(state, fake)
    fake.silent_updates.add("101")
    assert apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW) is False
    assert state.get("101").applied_start is None
    assert state.applied_outbox() == [("101", NOW + 3600, False, "Slurm did not keep the start time")]


def test_a_slurm_error_is_reported_and_the_job_is_left_alone(cfg, fake, slurm, state):
    track(state, fake)
    fake.fail_updates.add("101")
    assert apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW) is False
    assert state.get("101").applied_start is None
    assert state.applied_outbox()[0][2:] == (False, "Invalid user id for job 101")


def test_shadow_mode_never_defers(tmp_path, state):
    shadow, fake = make_cfg(tmp_path, "shadow"), FakeSlurm(NOW, allow_defer=False)
    track(state, fake)
    assert apply_decision(shadow, Slurm(fake), state, "101", NOW + 3600, NOW) is False
    assert updates(fake) == []
    assert state.applied_outbox() == [("101", NOW + 3600, False, "agent is in shadow mode")]


@pytest.mark.parametrize(
    "kw, why",
    [
        ({"state": "RUNNING"}, "running"),
        ({"skipped_reason": "array"}, "no longer managed"),
        ({"override": True}, "no longer managed"),
        ({"released": True}, "no longer managed"),
    ],
)
def test_jobs_that_are_not_ours_to_change_are_refused(cfg, fake, slurm, state, kw, why):
    t = track(state, fake)
    for k, v in kw.items():
        setattr(t, k, v)
    state.save(t)
    assert apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW) is False
    assert updates(fake) == [] and why in state.applied_outbox()[0][3]


def test_an_unknown_job_is_refused(cfg, fake, slurm, state):
    assert apply_decision(cfg, slurm, state, "555", NOW + 3600, NOW) is False
    assert state.applied_outbox()[0][3] == "not a job this agent manages"


def test_a_start_time_beyond_the_flex_limit_is_refused(cfg, fake, slurm, state):
    track(state, fake)
    too_far = NOW + 2 * 1440 * 60 + 301  # first_seen + 2 x max_wait + 5 minutes
    assert apply_decision(cfg, slurm, state, "101", too_far, NOW) is False
    assert updates(fake) == [] and "beyond the flex limit" in state.applied_outbox()[0][3]
    assert apply_decision(cfg, slurm, state, "101", NOW + 2 * 1440 * 60 + 300, NOW) is True  # exactly at the bound


def test_a_decision_for_now_releases_the_job(cfg, fake, slurm, state):
    track(state, fake)
    assert apply_decision(cfg, slurm, state, "101", NOW - 50, NOW) is True
    assert updates(fake) == [["scontrol", "update", "JobId=101", "StartTime=now"]]
    assert state.get("101").released is True


def test_a_repeated_decision_changes_nothing(cfg, fake, slurm, state):
    track(state, fake)
    apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW)
    apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW + 30)
    assert len(updates(fake)) == 1
    assert state.applied_outbox() == [("101", NOW + 3600, True, None)]


def test_release_all_undoes_only_our_own_pending_deferrals(cfg, fake, slurm, state):
    for ref in ("1", "2", "3", "4"):
        track(state, fake, ref)
    for ref in ("1", "2"):  # deferred by us
        apply_decision(cfg, slurm, state, ref, NOW + 3600, NOW)
    t4 = state.get("4")  # a job we deferred but that has since started
    t4.applied_start, t4.state = NOW + 100, "RUNNING"
    state.save(t4)
    fake.commands.clear()
    assert release_all(slurm, state) == 2
    assert sorted(c[2] for c in updates(fake)) == ["JobId=1", "JobId=2"]
    assert all(c[3] == "StartTime=now" for c in updates(fake))
    assert state.released_outbox() == ["1", "2"]
    assert state.get("1").released and not state.get("3").released  # "3" was never deferred by us
    assert release_all(slurm, state) == 0  # nothing left to release


def test_release_all_works_in_shadow_mode(tmp_path, state):
    fake = FakeSlurm(NOW, allow_defer=False)
    track(state, fake)
    t = state.get("101")
    t.applied_start = NOW + 3600  # deferred earlier, while the agent was autonomous
    state.save(t)
    assert release_all(Slurm(fake), state) == 1
    assert updates(fake) == [["scontrol", "update", "JobId=101", "StartTime=now"]]


def test_a_failed_release_is_retried_next_time(cfg, fake, slurm, state):
    track(state, fake)
    apply_decision(cfg, slurm, state, "101", NOW + 3600, NOW)
    fake.fail_updates.add("101")
    assert release_all(slurm, state) == 0 and state.get("101").released is False
    fake.fail_updates.clear()
    assert release_all(slurm, state) == 1
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_applier.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'wattshift_agent.applier'`.

- [ ] **Step 3: Write `applier.py`**

Create `agent/wattshift_agent/applier.py`:

```python
"""Turning cloud decisions into Slurm changes: set one start time, or undo one of our own (spec section 8)."""
from wattshift_agent.audit import event
from wattshift_agent.runner import SlurmError

NOW_SLACK_S = 5  # a decision this close to (or before) now is a release


def apply_decision(cfg, slurm, state, ref: str, start_at: int, now: int, audit=None) -> bool:
    """Apply one decision and queue the outcome for the next sync. Never raises for a refusal or a Slurm error: it
    reports ok=False with a reason, and the cloud then stops managing the job."""

    def done(ok: bool, error: str | None = None) -> bool:
        state.put_applied(ref, start_at, ok, error)
        event(audit, "apply", ref=ref, start_at=start_at, ok=ok, error=error)
        return ok

    if cfg.mode != "autonomous":
        return done(False, "agent is in shadow mode")
    t = state.get(ref)
    if t is None:
        return done(False, "not a job this agent manages")
    if t.state != "PENDING":
        return done(False, f"job is {t.state.lower()}, not pending")
    if t.skipped_reason or t.override or t.released:
        return done(False, "job is no longer managed")
    if start_at > t.first_seen + 2 * t.max_wait_min * 60 + 300:  # a safety net: latest = baseline + max_wait <= first_seen + 2 x max_wait
        return done(False, "start time is beyond the flex limit")
    if start_at <= now + NOW_SLACK_S:
        try:
            slurm.release(ref)
        except SlurmError as e:
            return done(False, str(e)[:200])
        t.released = True
        state.save(t)
        return done(True)
    if t.applied_start == start_at:
        return done(True)  # already set: a repeated decision
    try:
        slurm.set_start(ref, start_at)
        d = slurm.detail(ref)
    except SlurmError as e:
        return done(False, str(e)[:200])
    if d is None or d.eligible != start_at:
        return done(False, "Slurm did not keep the start time")
    t.applied_start = start_at
    state.save(t)
    return done(True)


def release_all(slurm, state, audit=None) -> int:
    """Set every job we deferred back to 'start now'. Allowed in every mode: it only undoes our own changes."""
    released = 0
    for t in state.active():
        if t.applied_start is None or t.released or t.state != "PENDING":
            continue
        try:
            slurm.release(t.ref)
        except SlurmError as e:
            event(audit, "release_failed", ref=t.ref, error=str(e)[:200])
            continue  # tried again next cycle; if the job has started meanwhile it is no longer pending and is skipped
        t.released = True
        state.save(t)
        state.add_released(t.ref)
        released += 1
        event(audit, "released", ref=t.ref)
    return released
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_applier.py -q`
Expected: all pass.

- [ ] **Step 5: Mutation-check the refusals**

Temporarily change `if cfg.mode != "autonomous":` to `if False:` and confirm `test_shadow_mode_never_defers` FAILS (the allow-list in the fake also raises `ForbiddenCommand`, which is the second line of defence); restore. Remove the `t.skipped_reason or t.override or t.released` check and confirm the three parametrized "no longer managed" cases FAIL; restore.

---

### Task 8: The cloud client and the full cycle

**Files:**
- Modify: `agent/wattshift_agent/client.py` (replace the Task 6 stub), `agent/wattshift_agent/cycle.py` (append `run_once`)
- Create: `agent/tests/test_client.py`, `agent/tests/test_run_once.py`

**Interfaces:**
- Consumes: everything from Tasks 2 to 7.
- Produces:
  - `client.CloudError(Exception)`; `client.Cloud(url, key, timeout=20)` with `post_sync(body: dict) -> dict` (sends `POST {url}/agent/v1/sync` with `X-Site-Key`, returns the parsed JSON reply, raises `CloudError` for any non-200, network error or bad JSON) and `health() -> None` (GET `/health`, raises `CloudError` on failure)
  - `cycle.CycleResult(next_poll_s, jobs, applied, released)` and `cycle.run_once(cfg, slurm, state, cloud, now, audit=None) -> CycleResult`

`run_once` order: `observe` (raises `SlurmError` if Slurm is unreadable: nothing is sent); in shadow mode `release_all` (undo any earlier deferral); `build_body`; `cloud.post_sync` (raises `CloudError`: state is already saved and the outboxes are kept); clear the outbox entries that were sent and mark finished jobs sent; clear the release request if the cloud acknowledged it; if the reply says `release_all` then `release_all` and ignore decisions; else in autonomous mode apply each decision, in shadow mode log and ignore them.

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_client.py`:

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from wattshift_agent.client import Cloud, CloudError

SEEN = []


class Handler(BaseHTTPRequestHandler):
    reply = (200, b'{"decisions": [], "release_all": false, "next_poll_s": 30}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append((self.path, self.headers["X-Site-Key"], self.headers["Content-Type"], body))
        code, data = Handler.reply
        self.send_response(code)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    SEEN.clear()
    Handler.reply = (200, b'{"decisions": [], "release_all": false, "next_poll_s": 30}')
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_post_sync_sends_the_key_and_returns_the_reply(server):
    out = Cloud(server, "wsk_abc").post_sync({"hello": 1})
    assert out == {"decisions": [], "release_all": False, "next_poll_s": 30}
    assert SEEN == [("/agent/v1/sync", "wsk_abc", "application/json", {"hello": 1})]


def test_an_http_error_is_a_cloud_error_with_the_status(server):
    Handler.reply = (401, b'{"detail": "missing or invalid X-Site-Key"}')
    with pytest.raises(CloudError, match="401"):
        Cloud(server, "wsk_bad").post_sync({})


def test_a_reply_that_is_not_json_is_a_cloud_error(server):
    Handler.reply = (200, b"<html>oops</html>")
    with pytest.raises(CloudError):
        Cloud(server, "k").post_sync({})


def test_an_unreachable_cloud_is_a_cloud_error():
    with pytest.raises(CloudError):
        Cloud("http://127.0.0.1:9", "k", timeout=2).post_sync({})


def test_health(server):
    Cloud(server, "k").health()
    with pytest.raises(CloudError):
        Cloud("http://127.0.0.1:9", "k", timeout=2).health()
```

Create `agent/tests/test_run_once.py`:

```python
import pytest

from tests.conftest import NOW, make_cfg
from tests.fakeslurm import FakeSlurm
from wattshift_agent.client import CloudError
from wattshift_agent.cycle import iso, run_once
from wattshift_agent.runner import SlurmError
from wattshift_agent.slurm import Slurm
from wattshift_agent.state import State, Tracked


def updates(fake):
    return [c for c in fake.commands if c[:2] == ["scontrol", "update"]]


def test_a_job_is_deferred_confirmed_started_and_reported(cfg, fake, slurm, state, cloud):
    fake.add("101", gpus=4, limit_min=60, runtime_s=300)
    cloud.reply(decisions=[{"ref": "101", "start_at": iso(NOW + 3600)}])
    r1 = run_once(cfg, slurm, state, cloud, NOW)
    assert [j["ref"] for j in cloud.bodies[0]["jobs"]] == ["101"] and cloud.bodies[0]["applied"] == []
    assert r1.applied == 1 and state.get("101").applied_start == NOW + 3600

    fake.advance(30)
    run_once(cfg, slurm, state, cloud, NOW + 30)
    assert cloud.bodies[1]["applied"] == [{"ref": "101", "start_at": iso(NOW + 3600), "ok": True, "error": None}]
    assert state.applied_outbox() == []  # the cloud accepted the report

    fake.advance(3600)  # Slurm starts it at the set time, with no help from the agent
    run_once(cfg, slurm, state, cloud, NOW + 3630)
    assert cloud.bodies[2]["jobs"][0]["state"] == "RUNNING"
    assert cloud.bodies[2]["jobs"][0]["start_time"] == iso(NOW + 3600)

    fake.advance(400)
    run_once(cfg, slurm, state, cloud, NOW + 4030)
    assert cloud.bodies[3]["jobs"][0]["state"] == "COMPLETED" and cloud.bodies[3]["jobs"][0]["end_time"] == iso(NOW + 3900)
    run_once(cfg, slurm, state, cloud, NOW + 4060)
    assert cloud.bodies[4]["jobs"] == []  # the final report was acknowledged, so it is not repeated
    assert len(updates(fake)) == 1


def test_a_cloud_outage_changes_nothing_in_slurm_and_keeps_the_reports(cfg, fake, slurm, state, cloud):
    fake.add("101")
    cloud.reply(decisions=[{"ref": "101", "start_at": iso(NOW + 3600)}])
    run_once(cfg, slurm, state, cloud, NOW)
    before = list(fake.commands)
    cloud.fail = True
    with pytest.raises(CloudError):
        run_once(cfg, slurm, state, cloud, NOW + 30)
    new = fake.commands[len(before):]
    assert all(c[0] in ("squeue", "sacct") or c[:3] == ["scontrol", "show", "job"] for c in new)  # reads only
    assert state.applied_outbox() == [("101", NOW + 3600, True, None)]  # still queued for the next successful sync
    assert fake.jobs["101"]["begin"] == NOW + 3600  # and the start time we set is still in force


def test_what_was_seen_before_an_outage_is_kept(cfg, fake, slurm, state, cloud):
    fake.add("101")
    cloud.fail = True
    with pytest.raises(CloudError):
        run_once(cfg, slurm, state, cloud, NOW)
    assert state.get("101") is not None


def test_a_slurm_failure_sends_nothing(cfg, fake, slurm, state, cloud):
    fake.down = True
    with pytest.raises(SlurmError):
        run_once(cfg, slurm, state, cloud, NOW)
    assert cloud.bodies == []


def test_shadow_mode_reports_but_ignores_decisions(tmp_path, cloud):
    cfg, fake = make_cfg(tmp_path, "shadow"), FakeSlurm(NOW, allow_defer=False)
    fake.add("101")
    cloud.reply(decisions=[{"ref": "101", "start_at": iso(NOW + 3600)}])  # a misbehaving cloud
    r = run_once(cfg, Slurm(fake), State(tmp_path / "s.db"), cloud, NOW)
    assert cloud.bodies[0]["mode"] == "shadow" and len(cloud.bodies[0]["jobs"]) == 1
    assert updates(fake) == [] and r.applied == 0


def test_switching_to_shadow_undoes_our_earlier_deferrals(tmp_path, cloud):
    cfg, fake, state = make_cfg(tmp_path, "shadow"), FakeSlurm(NOW, allow_defer=False), State(tmp_path / "s.db")
    fake.add("101")
    state.save(Tracked(ref="101", first_seen=NOW, max_wait_min=1440, state="PENDING", gpus=4, time_limit_min=60, applied_start=NOW + 3600))
    fake.user_set_start("101", NOW + 3600)  # the deferral is still in force in Slurm
    run_once(cfg, Slurm(fake), state, cloud, NOW)
    assert updates(fake) == [["scontrol", "update", "JobId=101", "StartTime=now"]]
    assert cloud.bodies[0]["released"] == ["101"]


def test_release_all_from_the_cloud_undoes_deferrals_and_ignores_decisions(cfg, fake, slurm, state, cloud):
    fake.add("101")
    fake.add("102")
    cloud.reply(decisions=[{"ref": "101", "start_at": iso(NOW + 3600)}])
    run_once(cfg, slurm, state, cloud, NOW)
    cloud.reply(release_all=True, decisions=[{"ref": "102", "start_at": iso(NOW + 7200)}])
    r = run_once(cfg, slurm, state, cloud, NOW + 30)
    assert r.released == 1 and state.get("102").applied_start is None  # the decision alongside release_all was ignored
    assert ["scontrol", "update", "JobId=101", "StartTime=now"] in fake.commands
    run_once(cfg, slurm, state, cloud, NOW + 60)
    assert cloud.bodies[2]["released"] == ["101"] and state.released_outbox() == []


def test_a_release_requested_by_the_operator_is_sent_until_acknowledged(cfg, fake, slurm, state, cloud):
    state.set_meta("release_requested", "1")
    run_once(cfg, slurm, state, cloud, NOW)  # the cloud has not turned its switch on yet
    assert cloud.bodies[0]["release_all"] is True and state.get_meta("release_requested") == "1"
    cloud.reply(release_all=True)
    run_once(cfg, slurm, state, cloud, NOW + 30)
    assert cloud.bodies[1]["release_all"] is True and state.get_meta("release_requested") == "0"
    run_once(cfg, slurm, state, cloud, NOW + 60)
    assert cloud.bodies[2]["release_all"] is False


def test_a_redelivered_decision_does_not_change_slurm_twice(cfg, fake, slurm, state, cloud):
    fake.add("101")
    d = {"decisions": [{"ref": "101", "start_at": iso(NOW + 3600)}]}
    cloud.reply(**d)
    cloud.reply(**d)
    run_once(cfg, slurm, state, cloud, NOW)
    run_once(cfg, slurm, state, cloud, NOW + 30)
    assert len(updates(fake)) == 1


@pytest.mark.parametrize("asked, want", [(5, 10), (30, 30), (9999, 120)])
def test_the_polling_interval_is_taken_from_the_cloud_within_limits(cfg, slurm, state, cloud, asked, want):
    cloud.reply(next_poll_s=asked)
    assert run_once(cfg, slurm, state, cloud, NOW).next_poll_s == want


def test_the_default_polling_interval_applies_when_the_cloud_says_nothing(cfg, slurm, state, cloud):
    cloud.replies.append({"next_poll_s": None})
    assert run_once(cfg, slurm, state, cloud, NOW).next_poll_s == cfg.poll_seconds
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_client.py tests/test_run_once.py -q`
Expected: FAIL, `ImportError: cannot import name 'Cloud'`.

- [ ] **Step 3: Write the client**

Replace `agent/wattshift_agent/client.py` with:

```python
"""The agent's only outbound connection: one JSON POST to the Wattshift cloud (stdlib only)."""
import json
import urllib.error
import urllib.request


class CloudError(Exception):
    """The cloud could not be reached, or answered with an error."""


class Cloud:
    def __init__(self, url: str, key: str, timeout: int = 20):
        self.url, self.key, self.timeout = url.rstrip("/"), key, timeout

    def _open(self, req):
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise CloudError(f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise CloudError(str(e))

    def post_sync(self, body: dict) -> dict:
        req = urllib.request.Request(
            f"{self.url}/agent/v1/sync", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Site-Key": self.key},
        )
        raw = self._open(req)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise CloudError(f"the cloud's reply was not JSON: {e}")

    def health(self) -> None:
        self._open(urllib.request.Request(f"{self.url}/health", method="GET"))
```

- [ ] **Step 4: Append `run_once` to `cycle.py`**

Add these imports at the top of `agent/wattshift_agent/cycle.py` (with the existing ones): `from wattshift_agent.applier import apply_decision, release_all`. Then append:

```python
@dataclass
class CycleResult:
    next_poll_s: int
    jobs: int
    applied: int
    released: int


def _parse_iso(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def _clamp_poll(asked, default: int) -> int:
    return default if not isinstance(asked, (int, float)) else int(min(120, max(10, asked)))


def run_once(cfg, slurm, state, cloud, now: int, audit=None) -> CycleResult:
    """Read Slurm, tell the cloud, apply the answer. Raises SlurmError (nothing is sent) or CloudError (the record and
    the outboxes are kept, and no job is changed). Either way every job keeps whatever start time it already has."""
    observe(cfg, slurm, state, now, audit)
    released = 0
    if cfg.mode == "shadow":  # shadow only reads; the one write it may make is undoing its own earlier deferrals
        released += release_all(slurm, state, audit)
    out = build_body(cfg, state, now)
    reply = cloud.post_sync(out.payload)

    state.clear_applied(out.applied)
    state.clear_released(out.released)
    state.mark_sent(out.final)
    if out.release_requested and reply.get("release_all"):
        state.set_meta("release_requested", "0")  # the cloud has its switch on; stop asking

    applied = 0
    decisions = reply.get("decisions") or []
    if reply.get("release_all"):
        released += release_all(slurm, state, audit)
    elif cfg.mode == "autonomous":
        for d in decisions:
            apply_decision(cfg, slurm, state, d["ref"], _parse_iso(d["start_at"]), now, audit)
            applied += 1
    elif decisions:
        event(audit, "decisions_ignored_in_shadow", count=len(decisions))
    state.prune(now - 7 * 86_400)
    return CycleResult(_clamp_poll(reply.get("next_poll_s"), cfg.poll_seconds), len(out.payload["jobs"]), applied, released)
```

- [ ] **Step 5: Run them and confirm they pass**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_client.py tests/test_run_once.py -q`
Expected: all pass.

- [ ] **Step 6: Mutation-check the fail-open and shadow guarantees**

Confirm each change makes exactly the named test fail, then restore:
- In `run_once`, call `apply_decision` even when `cfg.mode == "shadow"` (change `elif cfg.mode == "autonomous":` to `else:`): `test_shadow_mode_reports_but_ignores_decisions` fails.
- Move `state.clear_applied(out.applied)` above `cloud.post_sync(...)`: `test_a_cloud_outage_changes_nothing_in_slurm_and_keeps_the_reports` fails.
- Delete the `if reply.get("release_all"):` branch so decisions are applied even during a release: `test_release_all_from_the_cloud_undoes_deferrals_and_ignores_decisions` fails.

---

### Task 9: The command line

**Files:**
- Create: `agent/wattshift_agent/cli.py`, `agent/wattshift_agent/__main__.py`, `agent/tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `cli.Deps(slurm, state, cloud, audit)`; `cli.build_deps(cfg) -> Deps` (real subprocess runner with `allow_defer = cfg.mode == "autonomous"`, the real `Cloud`, an audit logger)
  - `cli.main(argv=None, *, factory=build_deps) -> int` with `wattshift-agent [-c agent.yaml] {run [--once] | release-all | status | check}`; exit code 0 on success, 1 when a cycle or a check fails, 2 on a config error
  - `python -m wattshift_agent` calls `main()`

`release-all` works without the cloud: it releases every job the agent deferred, sets the local `release_requested` flag, and the next sync tells the cloud to switch the site's kill switch on.

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_cli.py`:

```python
import time

import pytest

from tests.conftest import NOW
from tests.fakecloud import FakeCloud
from tests.fakeslurm import FakeSlurm
from wattshift_agent.cli import Deps, main
from wattshift_agent.cycle import iso
from wattshift_agent.slurm import Slurm
from wattshift_agent.state import State

CONFIG = """
cloud: {{url: 'http://cloud.test'}}
mode: {mode}
rules:
  - match: {{qos: flex}}
    max_wait: 24h
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WATTSHIFT_SITE_KEY", "wsk_test")
    fake, cloud = FakeSlurm(NOW), FakeCloud()

    def write(mode="autonomous"):
        p = tmp_path / "agent.yaml"
        p.write_text(CONFIG.format(mode=mode))
        return str(p)

    def factory(cfg):
        return Deps(Slurm(fake), State(cfg.state_path), cloud, None)

    return write, factory, fake, cloud, tmp_path


def run(env, *args, mode="autonomous"):
    write, factory, *_ = env
    return main(["-c", write(mode), *args], factory=factory)


def test_run_once_does_a_cycle(env):
    _, _, fake, cloud, _ = env
    fake.add("101")
    assert run(env, "run", "--once") == 0
    assert [j["ref"] for j in cloud.bodies[0]["jobs"]] == ["101"]


def test_run_once_fails_with_a_message_when_the_cloud_is_down(env, capsys):
    _, _, fake, cloud, _ = env
    cloud.fail = True
    assert run(env, "run", "--once") == 1
    assert "connection refused" in capsys.readouterr().err


def test_a_bad_config_exits_2(env, capsys):
    write, factory, *_ = env
    bad = write()
    open(bad, "w").write("mode: yolo\n")
    assert main(["-c", bad, "status"], factory=factory) == 2
    assert "config error" in capsys.readouterr().err


def test_release_all_works_without_the_cloud_and_asks_the_cloud_to_follow(env, capsys):
    _, factory, fake, cloud, tmp_path = env
    fake.add("101")
    # `run` uses the real clock, so the decision is an hour from the real now (not from the fake's fixed NOW)
    cloud.reply(decisions=[{"ref": "101", "start_at": iso(int(time.time()) + 3600)}])
    run(env, "run", "--once")
    cloud.fail = True  # the kill switch must not depend on the cloud
    fake.commands.clear()
    assert run(env, "release-all") == 0
    assert ["scontrol", "update", "JobId=101", "StartTime=now"] in fake.commands
    assert "released 1 job" in capsys.readouterr().out
    cloud.fail = False
    cloud.reply(release_all=True)
    run(env, "run", "--once")
    assert cloud.bodies[-1]["release_all"] is True and cloud.bodies[-1]["released"] == ["101"]


def test_status_summarises_the_record(env, capsys):
    _, _, fake, cloud, _ = env
    fake.add("101")
    fake.add("41_[1-3]")
    run(env, "run", "--once")
    capsys.readouterr()
    assert run(env, "status") == 0
    out = capsys.readouterr().out
    assert "PENDING" in out and "skipped" in out and "mode: autonomous" in out


def test_check_passes_on_a_healthy_setup(env, capsys):
    _, _, fake, cloud, _ = env
    fake.add("101")
    assert run(env, "check") == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and "squeue" in out and "cloud" in out


def test_check_fails_and_says_which_step_when_slurm_is_unreachable(env, capsys):
    _, _, fake, cloud, _ = env
    fake.down = True
    assert run(env, "check") == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "Unable to contact slurm controller" in out


def test_check_warns_when_times_do_not_come_back_as_epoch(env, capsys):
    _, _, fake, cloud, _ = env
    fake.add("101")
    fake.jobs["101"]["submit"] = "2026-09-21T14:13:20"  # a cluster that ignores SLURM_TIME_FORMAT
    assert run(env, "check") == 1
    assert "epoch" in capsys.readouterr().out
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_cli.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'wattshift_agent.cli'`.

- [ ] **Step 3: Write `cli.py` and `__main__.py`**

Create `agent/wattshift_agent/cli.py`:

```python
"""wattshift-agent: run the loop, release everything, show the record, or check the setup."""
import argparse
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass

from wattshift_agent.applier import release_all
from wattshift_agent.audit import make_audit_logger
from wattshift_agent.client import Cloud, CloudError
from wattshift_agent.config import Config, ConfigError, load_config
from wattshift_agent.cycle import run_once
from wattshift_agent.parse import ParseError
from wattshift_agent.rules import max_wait_for
from wattshift_agent.runner import SlurmError, SubprocessRunner
from wattshift_agent.slurm import Slurm
from wattshift_agent.state import State

log = logging.getLogger("wattshift")


@dataclass
class Deps:
    slurm: Slurm
    state: State
    cloud: object
    audit: object


def build_deps(cfg: Config) -> Deps:
    audit = make_audit_logger(cfg.audit_path)
    runner = SubprocessRunner(allow_defer=cfg.mode == "autonomous", prefix=cfg.slurm_prefix, audit=audit)
    return Deps(Slurm(runner), State(cfg.state_path), Cloud(cfg.cloud_url, cfg.site_key), audit)


def cmd_run(cfg: Config, deps: Deps, once: bool) -> int:
    while True:
        wait, ok = cfg.poll_seconds, True
        try:
            r = run_once(cfg, deps.slurm, deps.state, deps.cloud, int(time.time()), deps.audit)
            wait = r.next_poll_s
            log.info("cycle ok: %d job(s) reported, %d applied, %d released", r.jobs, r.applied, r.released)
        except (SlurmError, CloudError, ParseError) as e:
            ok = False
            print(f"cycle failed: {e}", file=sys.stderr)
            log.warning("cycle failed: %s", e)
        if once:
            return 0 if ok else 1
        time.sleep(wait)


def cmd_release_all(cfg: Config, deps: Deps) -> int:
    n = release_all(deps.slurm, deps.state, deps.audit)
    deps.state.set_meta("release_requested", "1")  # the next sync tells the cloud to switch its kill switch on
    print(f"released {n} job(s); the cloud will stop planning for this site at the next sync")
    return 0


def cmd_status(cfg: Config, deps: Deps) -> int:
    active = deps.state.active()
    by_state = Counter(t.state for t in active)
    print(f"mode: {cfg.mode}   cloud: {cfg.cloud_url}")
    print("tracked jobs: " + (", ".join(f"{n} {s}" for s, n in sorted(by_state.items())) or "none"))
    print(f"deferred by us and still pending: {sum(1 for t in active if t.applied_start and not t.released and t.state == 'PENDING')}")
    print(f"skipped (array, dependency, requeue): {sum(1 for t in active if t.skipped_reason)}")
    print(f"changed by their owner (no longer managed): {sum(1 for t in active if t.override)}")
    print(f"waiting to be reported: {len(deps.state.applied_outbox())} applied, {len(deps.state.released_outbox())} released")
    print(f"release requested: {deps.state.get_meta('release_requested') == '1'}")
    return 0


def cmd_check(cfg: Config, deps: Deps) -> int:
    """Try every capability the agent needs, so a wrong Slurm account or a wrong time format shows up now, not later."""
    failed = False

    def step(name, fn):
        nonlocal failed
        try:
            print(f"ok    {name}" + (f": {msg}" if (msg := fn()) else ""))
        except (SlurmError, CloudError, ParseError, OSError) as e:
            failed = True
            print(f"FAIL  {name}: {e}")

    rows = []

    def squeue():
        rows.extend(deps.slurm.queue())
        flex = [r for r in rows if r.state == "PENDING" and max_wait_for(cfg.rules, r)]
        return f"{len(rows)} job(s) visible, {len(flex)} pending job(s) match your rules"

    def epoch_times():
        if not rows:
            return "no jobs to check (submit one and run check again)"
        if any(r.submit is None for r in rows):
            raise ParseError("submit times did not come back as epoch seconds; make sure SLURM_TIME_FORMAT=%s reaches squeue (see slurm.prefix)")
        return "times are epoch seconds"

    def scontrol():
        pending = [r for r in rows if r.state == "PENDING" and r.ref.isdigit()]
        if not pending:
            return "no pending job to read"
        d = deps.slurm.detail(pending[0].ref)
        return "cannot read job details" if d is None else f"read job {pending[0].ref}: {d.gpus} GPU(s), limit {d.time_limit_min} min"

    def sacct():
        deps.slurm.finished(["1"])
        return "accounting readable"

    step("state file writable", lambda: deps.state.set_meta("check", "1"))
    step("squeue", squeue)
    if rows:
        step("epoch times", epoch_times)
    step("scontrol show job", scontrol)
    step("sacct", sacct)
    step("cloud reachable", lambda: deps.cloud.health())
    print(f"mode: {cfg.mode} ({'may' if cfg.mode == 'autonomous' else 'will never'} set future start times)")
    return 1 if failed else 0


def main(argv=None, *, factory=build_deps) -> int:
    p = argparse.ArgumentParser(prog="wattshift-agent")
    p.add_argument("-c", "--config", default="agent.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="poll Slurm and sync with the cloud")
    run.add_argument("--once", action="store_true", help="do one cycle and exit")
    sub.add_parser("release-all", help="set every job this agent deferred back to start now")
    sub.add_parser("status", help="show what the agent has recorded")
    sub.add_parser("check", help="verify Slurm access, time format and the cloud connection")
    a = p.parse_args(argv)
    try:
        cfg = load_config(a.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    deps = factory(cfg)
    if a.cmd == "run":
        try:
            return cmd_run(cfg, deps, a.once)
        except KeyboardInterrupt:
            return 0
    return {"release-all": cmd_release_all, "status": cmd_status, "check": cmd_check}[a.cmd](cfg, deps)
```

Create `agent/wattshift_agent/__main__.py`:

```python
import sys

from wattshift_agent.cli import main

sys.exit(main())
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `cd agent; .\.venv\Scripts\python -m pytest tests/test_cli.py -q`
Expected: all pass. (`test_check_warns_when_times_do_not_come_back_as_epoch` puts a non-numeric submit time into the fake; `squeue` prints it, `parse_queue` turns it into `submit=None`, and `check` reports that times are not epoch seconds.)

- [ ] **Step 5: Smoke-run the real entry point**

Run: `cd agent; .\.venv\Scripts\wattshift-agent --help` and `.\.venv\Scripts\python -m wattshift_agent -c does-not-exist.yaml status`
Expected: the first prints usage; the second prints `config error: cannot read ...` and exits with code 2.

---

### Task 10: The contract with the cloud

**Files:**
- Create: `agent/tests/test_contract.py`, `agent/tests/fixtures/contract/sync_all_kinds.json` (generated by the test, then reviewed), `backend/tests/test_agent_contract.py`

**Interfaces:**
- Consumes: `build_body`, `State`, `Tracked`, `iso`; the cloud's `agent_sync.SyncIn` and `process_sync`.
- Produces: one JSON request body that contains every kind of job fact, applied report, release and flag. The agent test proves the agent produces exactly that file; the backend test proves the cloud accepts it and reacts sensibly. If either side changes the contract, one of the two tests fails.

- [ ] **Step 1: The agent-side test**

Create `agent/tests/test_contract.py`:

```python
import json
import os
from pathlib import Path

from tests.conftest import NOW
from wattshift_agent.cycle import build_body
from wattshift_agent.state import Tracked

FILE = Path(__file__).parent / "fixtures" / "contract" / "sync_all_kinds.json"


def _tracked(state, ref, **kw):
    base = dict(first_seen=NOW - 60, max_wait_min=1440, state="PENDING", submit=NOW - 60, gpus=4, time_limit_min=60)
    state.save(Tracked(ref=ref, **{**base, **kw}))


def test_the_agents_request_matches_the_contract_file(cfg, state):
    _tracked(state, "48211", gpus=8, time_limit_min=90, predicted_start=NOW + 3600)  # a normal pending flex job
    _tracked(state, "48212", state="RUNNING", first_seen=NOW - 3000, submit=NOW - 3000, max_wait_min=720,
             applied_start=NOW - 600, start_time=NOW - 600)  # deferred by us and now running
    _tracked(state, "48213", state="COMPLETED", gpus=2, start_time=NOW - 1900, end_time=NOW - 100)  # finished
    _tracked(state, "50_[1-3]", gpus=None, time_limit_min=None, skipped_reason="array")
    _tracked(state, "48214", override=True, applied_start=NOW + 7200)  # the owner changed it
    _tracked(state, "48215", predicted_start=NOW - 60 + 365 * 86400)  # Slurm's bogus one-year prediction
    _tracked(state, "48217", released=True, applied_start=NOW - 1000)  # released by release-all
    state.put_applied("48211", NOW + 7200, True, None)
    state.put_applied("48214", NOW + 9000, False, "Invalid user id for job 48214")
    state.add_released("48217")
    state.set_meta("release_requested", "1")

    body = build_body(cfg, state, NOW).payload
    if os.environ.get("WATTSHIFT_WRITE_CONTRACT") == "1":  # regenerate deliberately, then review the diff
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps(body, indent=2) + "\n")
    assert body == json.loads(FILE.read_text())
```

- [ ] **Step 2: Generate the contract file and review it**

Run: `cd agent; $env:WATTSHIFT_WRITE_CONTRACT="1"; .\.venv\Scripts\python -m pytest tests/test_contract.py -q; Remove-Item Env:WATTSHIFT_WRITE_CONTRACT`
Then run it again without the variable: `.\.venv\Scripts\python -m pytest tests/test_contract.py -q`
Expected: passes both times. Open `agent/tests/fixtures/contract/sync_all_kinds.json` and check by eye that: `mode` is `autonomous`; there are seven jobs sorted by `ref`; job `48215` has `predicted_start: null` (the bogus prediction was dropped); job `48211` has a `predicted_start`; `50_[1-3]` has `skipped_reason: "array"`; `48214` has `override: true`; both `applied` reports are present; `released` is `["48217"]`; `release_all` is `true`; times end in `Z`.

- [ ] **Step 3: The cloud-side test**

Create `backend/tests/test_agent_contract.py`:

```python
"""The request the real agent builds (agent/tests/fixtures/contract/sync_all_kinds.json, generated by the agent's own
tests) is accepted by the cloud and handled as the contract says."""
import json
from datetime import timedelta
from pathlib import Path

import pytest

from app import agent_sync, sites
from app.catalogue import seed_catalogue
from app.models import ManagedJob
from app.seed import seed_tod
from tests.helpers import NOW, add_prices, evening_to_next_noon

FILE = Path(__file__).resolve().parents[2] / "agent" / "tests" / "fixtures" / "contract" / "sync_all_kinds.json"


@pytest.fixture()
def world(session):
    seed_tod(session)
    seed_catalogue(session)
    add_prices(session, NOW, evening_to_next_noon())
    site, _ = sites.create_site(session, "Acme", "Pune-1", gpus=64, mode="autonomous")
    return session, site


def status(session, ref):
    session.expire_all()
    j = session.query(ManagedJob).filter_by(ref=ref).one()
    return j.state, j.plan_status


def test_the_cloud_accepts_and_understands_the_agents_request(world):
    session, site = world
    body = agent_sync.SyncIn.model_validate(json.loads(FILE.read_text()))
    out = agent_sync.process_sync(session, site, body, NOW)

    assert out.release_all is True and out.decisions == []  # the request asked for release_all, so the kill switch is on
    assert status(session, "50_[1-3]") == ("PENDING", "skipped")
    assert status(session, "48214") == ("PENDING", "abandoned")  # user override, and the failed apply
    assert status(session, "48217")[1] == "released"
    assert status(session, "48213")[0] == "COMPLETED"
    job = session.query(ManagedJob).filter_by(ref="48213").one()
    assert job.actual_start is not None and job.actual_end is not None and job.gpus == 2
    assert session.query(ManagedJob).filter_by(ref="48211").one().applied_start is not None

    sites.set_release_all(session, site, False, NOW + timedelta(seconds=30))  # once an operator clears the switch...
    again = body.model_copy(update={"release_all": False, "applied": [], "released": []})
    out = agent_sync.process_sync(session, site, again, NOW + timedelta(seconds=60))
    assert out.release_all is False
    assert {d.ref for d in out.decisions} <= {"48211", "48215"}  # ...only still-pending, still-managed jobs are planned
```

- [ ] **Step 4: Run both sides and the whole agent suite**

Run:

```powershell
cd agent; .\.venv\Scripts\python -m pytest -q
cd ..\backend; .\.venv\Scripts\python -m pytest tests/test_agent_contract.py -q
```

Expected: everything passes. If the backend test fails on a field, the contract file and the cloud disagree: decide which side is right (the spec section 6 plus the additions in the cloud-core plan are the reference), fix that side, regenerate the JSON only if the agent side changed deliberately.

---

### Task 11: Docs, spec updates, full run and report

**Files:**
- Create: `agent/README.md`, `agent/agent.example.yaml`
- Modify: `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md`

- [ ] **Step 1: The example config**

Create `agent/agent.example.yaml`:

```yaml
# Wattshift agent configuration. Start with mode: shadow (it only reads and reports; nothing in Slurm changes).
cloud:
  url: https://api.wattshift.example        # given to you at onboarding
  key_file: /etc/wattshift/site.key         # the site key, one line. Or set WATTSHIFT_SITE_KEY. Never put it in this file.

mode: shadow                                # shadow | autonomous
poll_seconds: 30                            # 10 to 300; the cloud may ask for a different pace (10 to 120)

state_file: /var/lib/wattshift/state.db     # the agent's memory; losing it is safe
audit_file: /var/log/wattshift/audit.log    # every Slurm command and decision, one JSON line each

# How to reach Slurm's commands when they are not on this machine. Leave empty if squeue is on the PATH.
# Whatever you use must pass TZ=UTC and SLURM_TIME_FORMAT=%s through to the Slurm command, for example:
#   prefix: [docker, exec, -e, TZ=UTC, -e, SLURM_TIME_FORMAT=%s, slurmctld]
slurm:
  prefix: []

# Only jobs that match a rule are ever touched. The first matching rule wins. Everything else is invisible to Wattshift.
# match fields: qos, partition, user, account (exact) and name (a regular expression).
# max_wait: how much later than it otherwise would have started a job may start (e.g. 90m, 12h, 2d). 0h = never touch.
rules:
  - match: { qos: flex }
    max_wait: 24h
  - match: { partition: batch-infer, name: "^eval-.*" }
    max_wait: 12h
default: none
```

- [ ] **Step 2: The README**

Create `agent/README.md` with these sections, in this order and in plain language:

1. **What it is** (three sentences: it runs inside your network next to Slurm, makes outbound HTTPS calls only, and changes only the start time of jobs your rules allow).
2. **Install**: `python -m venv /opt/wattshift && /opt/wattshift/bin/pip install .` (on Windows `python -m venv agent\.venv` then `pip install -e ".[dev]"`); Python 3.10 or newer.
3. **The Slurm account it needs**: an account with **Operator** level (`sacctmgr modify user <name> set adminlevel=Operator`), because a plain user cannot see other users' pending jobs when `PrivateData` is set, and only an Operator can change another user's start time. Say plainly that Slurm has no permission that allows only deferring: an Operator can also cancel jobs, which is why the agent has an allow-list, an audit log and a shadow mode you can inspect first.
4. **Exactly what it runs** (copy the table from the "Slurm commands" section of this plan, plus the rule that nothing else is ever executed, and that `mode: shadow` cannot set a future start time).
5. **Modes**: shadow (default) and autonomous, and how the cloud and the agent must both agree before anything is deferred.
6. **First run**: `wattshift-agent -c agent.yaml check`, then `wattshift-agent -c agent.yaml run --once`, then `run` under a service manager (include a 10-line systemd unit with `Restart=always`).
7. **The kill switch**: `wattshift-agent -c agent.yaml release-all` sets every job the agent deferred to start now, works without the cloud, and tells the cloud to stop planning for the site. Also: stop the agent and every start time already set stays in force and is honoured by Slurm.
8. **What leaves your network**: the list from Global Constraints ("Privacy"), and what never does (user, account, job name, script).
9. **Troubleshooting**: `check` output meanings; audit log location; `status`.

- [ ] **Step 3: Update the spec**

In `docs/superpowers/specs/2026-09-19-slurm-agent-automation-design.md`:
- Section 5: after the YAML example add: "The rules live in the agent's single config file (`agent.yaml`, see `agent/agent.example.yaml`) next to the cloud URL, the mode and how to reach Slurm. `name` is a regular expression; `qos`, `partition`, `user` and `account` are exact. `max_wait: 0` explicitly excludes what it matches."
- Section 8, last row ("Commands the agent runs"): replace the allow-list with: "Only: `squeue`, `sacct`, `scontrol show job <id>`, and `scontrol update JobId=<id> StartTime=<value>` where `now` (a release) is always allowed, so a shadow agent can undo its own earlier deferrals, and an absolute UTC time (a deferral) only in autonomous mode. The `Comment=wattshift:managed` marker was dropped: it is weak (Spike 0) and overwriting a user's comment is a change we do not need to make."
- Section 13 (Code layout): replace the `agent/` bullet with the module list from this plan's File structure.
- Section 15: mark step 3 "**Agent: DONE <date>** (plan: `docs/superpowers/plans/2026-09-20-slurm-agent.md`); validated against the in-process fake Slurm; the real-Slurm run is step 5."
- Section 16: add under "Still to verify": "the exact `squeue -o` fields, `sacct -S now-14days` and `TresPerJob` on a real Slurm (isolated in `agent/wattshift_agent/parse.py` and `slurm.py`)."

- [ ] **Step 4: Full run**

Run:

```powershell
cd agent; .\.venv\Scripts\python -m pytest -q
cd ..\backend; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider
```

Expected: the whole agent suite passes; the backend suite passes (246 plus the new contract test).

- [ ] **Step 5: Add a progress note to the project plan file**

Append to `C:\Users\aakur\.claude\plans\analyse-all-the-files-snuggly-haven.md` under the "AUTOMATION ARC" heading: agent built (module list, test count), what was verified only against the fake Slurm, the three unverified Slurm assumptions, and "next: measurement and dashboard, then the end-to-end proof on the Docker Slurm (`scratchpad/slurm-lab`; its volumes were removed in Spike 0, so it must be re-created from `helpers.ps1`, the patched entrypoint and `docker-compose.override.yml`)".

- [ ] **Step 6: Report**

Tell the user, in plain words: what the agent can now do, the test counts, that it has only been exercised against a fake Slurm (the real one is the end-to-end plan), the three unverified Slurm assumptions, and what comes next. Do not commit.

---

## Self-review

**Spec coverage.** Section 5 rules (Task 2); section 6 contract, UTC application, live-view GPUs, predicted-start guard, poll cadence, sacct for finished jobs, prefix-matched states, the fields read (Tasks 3, 4, 6, 8); section 8 rows: no decision leaves a job alone, cloud/agent death leaves start times in force (Task 8 outage test), cannot apply so leave alone (Task 7), user override from the agent's own record (Task 6), release_all (Tasks 7, 8, 9), decision after start ignored (Task 7 `job is running`), arrays, dependencies and requeues skipped (Task 6), the allow-list and audit (Task 4); section 9 shadow has no defer path, and the exception for undoing its own deferrals (Tasks 4, 7, 8); section 13 code layout; section 14 items 1 to 3 (parsers on recorded formats, agent tests against a fake Slurm, contract tests) with item 4 (real Docker Slurm) left to the end-to-end plan; section 17 criteria 1, 3, 4, 5 are each exercised here (shadow changes nothing, outage keeps start times, release restores, override respected); criterion 2 and 6 belong to the later plans.

**Placeholders.** None. Where a test depends on a fake detail there is an explicit fallback instruction (Task 3 node count, Task 6 vanished job, Task 9 epoch check).

**Type consistency.** `Tracked` field names are used identically in `state.py`, `cycle.py`, `applier.py`, and the tests. `QueueRow`, `JobDetail`, `AcctRow` fields match their uses (`r.start`, `d.eligible`, `a.end`). `apply_decision(cfg, slurm, state, ref, start_at, now, audit)` and `release_all(slurm, state, audit)` match their call sites in `run_once`. `run_once(cfg, slurm, state, cloud, now, audit)` matches the CLI and tests. The request body keys match the cloud's `SyncIn`/`JobFact`/`Applied` (Task 10 proves it).

**Known limits, stated once.** Only the fake Slurm has been exercised; `squeue` field set, `sacct -S now-14days` and `TresPerJob` are unverified on a real cluster. The state file is one SQLite connection per process (one agent per site). A single cycle reads at most 200 new jobs and sends at most 2000 facts. Job names containing spaces cannot affect `scontrol` parsing because names come from `squeue`.
