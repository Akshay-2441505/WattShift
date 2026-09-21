import json
import os
from pathlib import Path

from tests.conftest import NOW
from wattshift_agent.cycle import build_body
from wattshift_agent.state import Tracked

FILE = Path(__file__).parent / "fixtures" / "contract" / "sync_all_kinds.json"


def _tracked(store, ref, **kw):
    base = dict(first_seen=NOW - 60, max_wait_min=1440, state="PENDING", submit=NOW - 60, gpus=4, time_limit_min=60)
    store.save(Tracked(ref=ref, **{**base, **kw}))


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
