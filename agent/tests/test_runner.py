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
        ["scontrol", "update", "JobId=5", "Priority=0now"],  # ends in "now" after 10 characters, but is not StartTime=
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
