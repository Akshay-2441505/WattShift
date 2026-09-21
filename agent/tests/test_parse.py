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
