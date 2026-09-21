import json
import subprocess
import uuid
from pathlib import Path
from unittest import mock

import pytest

from app.models import Job
from app.providers.kaggle import KaggleProvider, parse_status


def ok(stdout="", stderr=""):
    return subprocess.CompletedProcess([], 0, stdout, stderr)


def make_job():
    return Job(id=uuid.UUID("a3daf4ba-a341-4d28-b20d-efb2a7d03b41"), duration_minutes=60)


@pytest.mark.parametrize(
    "out, expected",
    [
        ('akshayakurdekar/x has status "KernelWorkerStatus.COMPLETE"', "done"),
        ('akshayakurdekar/x has status "KernelWorkerStatus.RUNNING"', "running"),
        ('akshayakurdekar/x has status "KernelWorkerStatus.QUEUED"', "running"),
        ('akshayakurdekar/x has status "KernelWorkerStatus.ERROR"', "failed"),
        ('akshayakurdekar/x has status "KernelWorkerStatus.CANCEL_ACKNOWLEDGED"', "failed"),
        ('akshayakurdekar/x has status "KernelWorkerStatus.CANCEL_REQUESTED"', "failed"),
    ],
)
def test_parse_status(out, expected):
    assert parse_status(out) == expected


@pytest.mark.parametrize("out", ["Not found", "", 'has status "KernelWorkerStatus.SOMETHING_NEW"'])
def test_unrecognised_status_raises_so_the_poller_retries(out):
    with pytest.raises(ValueError):
        parse_status(out)


def test_start_stages_a_private_gpu_kernel_and_pushes_it():
    seen = {}

    def fake_run(cmd, **kw):
        d = Path(cmd[cmd.index("-p") + 1])
        seen["cmd"] = cmd
        seen["meta"] = json.loads((d / "kernel-metadata.json").read_text())
        seen["script"] = (d / seen["meta"]["code_file"]).read_text()
        return ok("Kernel version 1 successfully pushed.")

    p = KaggleProvider("someone", run_seconds=25, timeout_seconds=300, exe="kaggle-bin")
    with mock.patch("app.providers.kaggle.subprocess.run", fake_run):
        ref = p.start(make_job())

    slug = "wattshift-a3daf4baa3"
    assert ref == f"someone/{slug}"
    m = seen["meta"]
    assert m["id"] == ref and m["title"] == slug  # Kaggle derives the slug from the title; they must agree
    assert m["enable_gpu"] == "true" and m["is_private"] == "true" and m["enable_internet"] == "false"
    assert seen["cmd"][:3] == ["kaggle-bin", "kernels", "push"]
    assert seen["cmd"][-2:] == ["-t", "300"]
    assert "RUN_SECONDS = 25" in seen["script"]
    assert "a3daf4ba-a341-4d28-b20d-efb2a7d03b41" in seen["script"]
    assert "__JOB_ID__" not in seen["script"] and "__RUN_SECONDS__" not in seen["script"]  # all placeholders filled


def test_start_raises_with_kaggle_stderr_on_failure():
    p = KaggleProvider("someone", exe="kaggle-bin")
    bad = subprocess.CompletedProcess([], 1, "", "403 Forbidden: phone verification required")
    with mock.patch("app.providers.kaggle.subprocess.run", return_value=bad):
        with pytest.raises(RuntimeError, match="phone verification"):
            p.start(make_job())


def test_start_requires_a_username():
    with pytest.raises(ValueError):
        KaggleProvider("")


def test_status_shells_out_and_parses():
    p = KaggleProvider("someone", exe="kaggle-bin")
    with mock.patch(
        "app.providers.kaggle.subprocess.run", return_value=ok('someone/x has status "KernelWorkerStatus.COMPLETE"')
    ) as run:
        assert p.status("someone/x") == "done"
    assert run.call_args.args[0] == ["kaggle-bin", "kernels", "status", "someone/x"]


def test_status_404_is_a_lookup_error_so_the_dispatcher_can_give_up_on_it():
    p = KaggleProvider("someone", exe="kaggle-bin")
    gone = subprocess.CompletedProcess([], 1, "", "404 Client Error: Not Found for url: https://api.kaggle.com/x")
    with mock.patch("app.providers.kaggle.subprocess.run", return_value=gone):
        with pytest.raises(LookupError):
            p.status("someone/x")


def test_status_nonzero_exit_raises():
    p = KaggleProvider("someone", exe="kaggle-bin")
    with mock.patch("app.providers.kaggle.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "boom")):
        with pytest.raises(RuntimeError, match="boom"):
            p.status("someone/x")
