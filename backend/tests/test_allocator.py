from datetime import datetime, timedelta, timezone

import pytest

from app.allocator import Block, NoCapacityError, allocate, jitter_minutes, overlaps

T0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
STEP = timedelta(minutes=15)


def blocks(rates, start=T0):
    return [Block(start + i * STEP, r) for i, r in enumerate(rates)]


def alloc(job_id="j", dur=15, deadline=None, bl=None, ledger=None, cap=15, earliest=T0):
    bl = bl if bl is not None else blocks([5, 3, 9, 4])
    deadline = deadline or bl[-1].start + STEP
    return allocate(job_id, dur, earliest, deadline, bl, {} if ledger is None else ledger, cap)


def test_picks_cheapest_block():
    j = jitter_minutes("j")
    assert alloc() == T0 + STEP + timedelta(minutes=j)


Z = next(f"z{i}" for i in range(1000) if jitter_minutes(f"z{i}") == 0)  # block-aligned job id


def test_spills_to_next_cheapest_when_full():
    # cheapest block (index 1) is full -> next cheapest is index 3 (rate 4)
    assert alloc(Z, ledger={T0 + STEP: 15}) == T0 + 3 * STEP


def test_jittered_job_straddling_a_full_block_is_excluded():
    j = jitter_minutes("j")
    assert j > 0
    s = alloc("j", ledger={T0 + STEP: 15}, bl=blocks([5, 3, 9, 4, 8, 8]))
    assert not any(bs == T0 + STEP for bs, _ in overlaps(s, 15))


def test_two_jobs_tight_cap_land_in_different_windows():
    ledger = {}
    a = alloc("a", ledger=ledger)
    for bs, m in overlaps(a, 15):  # caller records consumption
        ledger[bs] = ledger.get(bs, 0) + m
    b_start = alloc("b", ledger=ledger)
    assert (a - timedelta(minutes=jitter_minutes("a"))) != (b_start - timedelta(minutes=jitter_minutes("b")))


def test_job_spanning_two_blocks_needs_both_free():
    # block 1 is full: a 30-min job may not touch it, so start 0 (blocks 0,1) and 1 are out;
    # of starts 2,3,4 the cheapest pair is (4,8) at index 3
    bl = blocks([5, 3, 9, 4, 8, 8])
    assert alloc(Z, dur=30, bl=bl, ledger={T0 + STEP: 15}, cap=15) == T0 + 3 * STEP


def test_deadline_respected():
    deadline = T0 + timedelta(minutes=20)
    # only block 0 fits before the deadline for a 15-min job (block 1 starts at +15, ends +30 > +20)
    s = alloc("j", dur=15, deadline=deadline, bl=blocks([9, 1, 1, 1]))
    assert s + timedelta(minutes=15) <= deadline


def test_unpriced_gap_is_never_spanned():
    bl = [Block(T0, 5), Block(T0 + 2 * STEP, 1)]  # no price for the +15 block
    # a 30-min job needs 2 contiguous priced blocks; none exist -> NoCapacityError, not a guess
    with pytest.raises(NoCapacityError):
        alloc("j", dur=30, bl=bl, deadline=T0 + 4 * STEP, cap=30)
    # a 1-min job fits inside a single priced block and picks the cheaper one
    assert alloc("j", dur=1, bl=bl, deadline=T0 + 4 * STEP) >= T0 + 2 * STEP


def test_no_capacity_raises():
    ledger = {b.start: 15 for b in blocks([5, 3, 9, 4])}
    with pytest.raises(NoCapacityError):
        alloc(ledger=ledger)


def test_weight_makes_big_jobs_use_more_of_a_blocks_capacity():
    """A 4-unit-wide job consumes 4x per minute, so cap 30 fits one 15-min 2-wide job but not two 4-wide ones."""
    ledger = {}
    a = alloc(Z, cap=60, ledger=ledger)  # weight defaults to 1
    assert a is not None
    ledger = {T0 + STEP: 45}  # cheapest block already holds 45 units
    # a weight-1 job (15 units) still fits under cap 60 there; a weight-2 job (30 units) does not and spills
    fits = allocate(Z, 15, T0, T0 + 4 * STEP, blocks([5, 3, 9, 4]), dict(ledger), 60, weight=1)
    spills = allocate(Z, 15, T0, T0 + 4 * STEP, blocks([5, 3, 9, 4]), dict(ledger), 60, weight=2)
    assert fits == T0 + STEP and spills == T0 + 3 * STEP


def test_deterministic_and_jitter_stable():
    assert alloc("x") == alloc("x")
    assert jitter_minutes("x") == jitter_minutes("x")
    assert 0 <= jitter_minutes("anything") < 15


def test_earliest_respected():
    earliest = T0 + 2 * STEP
    s = alloc("j", earliest=earliest)
    assert s >= earliest
