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
