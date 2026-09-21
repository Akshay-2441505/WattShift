"""The agent's parsers against outputs recorded from a REAL Slurm (e2e/capture_formats.py)."""
import json
from pathlib import Path

import pytest

from wattshift_agent import parse
from wattshift_agent.parse import parse_acct, parse_detail, parse_queue

REAL = Path(__file__).parent / "fixtures" / "real"
pytestmark = pytest.mark.skipif(not (REAL / "index.json").exists(), reason="real fixtures not recorded")


def ids():
    return json.loads((REAL / "index.json").read_text())


def read(name):
    return (REAL / name).read_text(encoding="utf-8")


def rows():
    return {r.name: r for r in parse_queue(read("squeue_all.txt"))}


def test_the_queue_parses_and_times_are_epoch_seconds():
    by_name = rows()
    assert {"e2e-gres3", "e2e-gpus2", "e2e-normal", "e2e-array", "e2e-begin", "e2e-dependent"} <= set(by_name)
    r = by_name["e2e-gres3"]
    assert (r.state, r.user, r.account, r.qos, r.partition) == ("PENDING", "alice", "labs", "flex", "cpu")
    assert isinstance(r.submit, int) and r.submit > 1_700_000_000  # %V came back as epoch seconds
    assert by_name["e2e-normal"].qos == "normal"


def test_special_rows():
    by_name = rows()
    assert "_" in by_name["e2e-array"].ref  # a pending array shows as <id>_[1-3]
    assert by_name["e2e-dependent"].reason == "Dependency"
    assert by_name["e2e-begin"].reason == "BeginTime" and isinstance(by_name["e2e-begin"].start, int)  # a user's --begin shows in %S
    assert "e2e name | with bar" in by_name  # a | inside the job name survives (name is the last field)


def test_running_blockers_are_listed_with_a_real_start():
    running = [r for r in rows().values() if r.state == "RUNNING"]
    assert len(running) == 2 and all(isinstance(r.start, int) for r in running)


def test_gpu_counts_from_real_scontrol_output():
    assert parse_detail(read("scontrol_gres3.txt"), str(ids()["gres3"])).gpus == 3
    assert parse_detail(read("scontrol_gpus2.txt"), str(ids()["gpus2"])).gpus == 2  # --gpus style (TresPerJob or as recorded)


def test_detail_flags_from_real_scontrol_output():
    d = parse_detail(read("scontrol_dependent.txt"), str(ids()["dependent"]))
    assert d.dependency is True and d.reason == "Dependency"
    b = parse_detail(read("scontrol_begin.txt"), str(ids()["begin"]))
    assert b.reason == "BeginTime" and isinstance(b.eligible, int) and b.eligible > b.submit
    g = parse_detail(read("scontrol_gres3.txt"), str(ids()["gres3"]))
    assert g.dependency is False and g.restarts == 0 and g.time_limit_min == 5
    assert parse_detail(read("scontrol_array.txt"), str(ids()["array"])).array is True


def test_accounting_rows_from_real_sacct_output():
    rows_ = {r.ref: r for r in parse_acct(read("sacct_finished.txt"))}
    i = ids()
    assert rows_[str(i["done"])].state == "COMPLETED" and rows_[str(i["done"])].end
    assert rows_[str(i["failed"])].state == "FAILED"
    assert rows_[str(i["timeout"])].state == "TIMEOUT"
    assert rows_[str(i["cancel_run"])].state == "CANCELLED" and rows_[str(i["cancel_run"])].start
    assert rows_[str(i["cancel_pend"])].state == "CANCELLED" and rows_[str(i["cancel_pend"])].start is None  # never started


def test_an_unknown_job_is_reported_the_way_the_facade_expects():
    text = read("scontrol_invalid.txt")
    assert "exit=1" in text and "Invalid job id" in text  # slurm.Slurm.detail() treats exactly this as "job is gone"
