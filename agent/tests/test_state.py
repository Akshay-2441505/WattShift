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
