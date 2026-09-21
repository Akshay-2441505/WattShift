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
