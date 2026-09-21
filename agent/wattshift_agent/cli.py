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
            msg = fn()
            print(f"ok    {name}" + (f": {msg}" if msg else ""))
        except (SlurmError, CloudError, ParseError, OSError) as e:
            failed = True
            print(f"FAIL  {name}: {e}")

    rows = []

    def squeue():
        rows.extend(deps.slurm.queue())
        flex = [r for r in rows if r.state == "PENDING" and max_wait_for(cfg.rules, r)]
        return f"{len(rows)} job(s) visible, {len(flex)} pending job(s) match your rules"

    def epoch_times():
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
