import os
import pytest

from wattshift_agent.config import ConfigError, load_config, parse_minutes

GOOD = """
cloud:
  url: https://api.example.test/
  key_file: {key_file}
mode: autonomous
poll_seconds: 45
state_file: st.db
audit_file: {audit}
slurm:
  prefix: [ssh, headnode]
rules:
  - match: {{ qos: flex }}
    max_wait: 24h
  - match: {{ partition: batch-infer, name: "^eval-.*" }}
    max_wait: 12h
default: none
"""


def write(tmp_path, text, key="wsk_abc\n"):
    (tmp_path / "site.key").write_text(key)
    p = tmp_path / "agent.yaml"
    p.write_text(text.format(key_file=tmp_path / "site.key", audit=tmp_path / "a.log"))
    return p


@pytest.mark.parametrize("text, want", [("90m", 90), ("12h", 720), ("2d", 2880), (" 5 h ", 300), ("0h", 0)])
def test_durations(text, want):
    assert parse_minutes(text) == want


@pytest.mark.parametrize("text", ["", "12", "h", "1.5h", "-2h", "12 hours", "1w"])
def test_bad_durations(text):
    with pytest.raises(ConfigError):
        parse_minutes(text)


def test_a_full_config_loads(tmp_path):
    cfg = load_config(write(tmp_path, GOOD), environ={})
    assert cfg.cloud_url == "https://api.example.test" and cfg.site_key == "wsk_abc"
    assert cfg.mode == "autonomous" and cfg.poll_seconds == 45 and cfg.slurm_prefix == ("ssh", "headnode")
    assert cfg.state_path.name == "st.db" and cfg.state_path.parent == tmp_path.resolve()  # relative paths sit next to the config
    assert [r.max_wait_min for r in cfg.rules] == [1440, 720]
    assert cfg.rules[0].exact == (("qos", "flex"),) and cfg.rules[0].name_re is None
    assert cfg.rules[1].exact == (("partition", "batch-infer"),) and cfg.rules[1].name_re.search("eval-7")


def test_the_environment_key_beats_the_key_file(tmp_path):
    cfg = load_config(write(tmp_path, GOOD), environ={"WATTSHIFT_SITE_KEY": " wsk_env "})
    assert cfg.site_key == "wsk_env"


def test_mode_defaults_to_shadow_and_paths_default_next_to_the_config(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("cloud: {url: 'http://localhost'}\nrules: []\n")
    cfg = load_config(p, environ={"WATTSHIFT_SITE_KEY": "k"})
    assert cfg.mode == "shadow" and cfg.rules == () and cfg.poll_seconds == 30
    assert cfg.state_path.name == "state.db" and cfg.audit_path.name == "audit.log"


@pytest.mark.parametrize(
    "patch, msg",
    [
        ("mode: yolo\n", "mode must be"),
        ("poll_seconds: 5\n", "poll_seconds"),
        ("poll_seconds: fast\n", "poll_seconds"),
        ("default: allow\n", "default must be none"),
        ("colour: red\n", "unknown setting"),
        ("slurm: {shell: bash}\n", "slurm takes only prefix"),
        ("rules:\n  - match: {qoss: flex}\n    max_wait: 1h\n", "unknown field"),
        ("rules:\n  - match: {}\n    max_wait: 1h\n", "at least one field"),
        ("rules:\n  - match: {qos: flex}\n", "exactly two keys"),
        ("rules:\n  - match: {qos: flex}\n    max_wait: soon\n", "bad duration"),
        ("rules:\n  - match: {name: '('}\n    max_wait: 1h\n", "regular expression"),
        ("rules: nope\n", "rules must be a list"),
    ],
)
def test_mistakes_are_named(tmp_path, patch, msg):
    p = tmp_path / "agent.yaml"
    p.write_text("cloud: {url: 'http://localhost'}\n" + patch)
    with pytest.raises(ConfigError, match=msg):
        load_config(p, environ={"WATTSHIFT_SITE_KEY": "k"})


def test_a_key_inside_the_file_is_refused(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("cloud: {url: 'http://localhost', key: wsk_secret}\n")
    with pytest.raises(ConfigError, match="never goes in this file"):
        load_config(p, environ={})


def test_no_key_anywhere_is_an_error(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("cloud: {url: 'http://localhost'}\n")
    with pytest.raises(ConfigError, match="no site key"):
        load_config(p, environ={})


def test_bad_url_and_missing_file(tmp_path):
    p = tmp_path / "agent.yaml"
    p.write_text("cloud: {url: 'api.example.test'}\n")
    with pytest.raises(ConfigError, match="http"):
        load_config(p, environ={"WATTSHIFT_SITE_KEY": "k"})
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml", environ={})


@pytest.mark.parametrize("url", ["http://api.wattshift.example", "http://10.0.0.5:8000", "http://localhost.evil.example"])
def test_a_plain_http_cloud_address_is_refused_unless_it_is_this_machine(tmp_path, url):
    p = write(tmp_path, GOOD.replace("https://api.example.test/", url))
    with pytest.raises(ConfigError, match="https://"):
        load_config(p, environ={})


@pytest.mark.parametrize("url", ["https://api.wattshift.example", "http://localhost:8100", "http://127.0.0.1:8100", "http://[::1]:8100"])
def test_https_and_local_http_addresses_are_accepted(tmp_path, url):
    load_config(write(tmp_path, GOOD.replace("https://api.example.test/", url)), environ={})


@pytest.mark.skipif(os.name == "nt", reason="Windows has no group/world permission bits to check")
def test_a_key_file_other_users_can_read_is_refused(tmp_path):
    p = write(tmp_path, GOOD)
    (tmp_path / "site.key").chmod(0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_config(p, environ={})
    (tmp_path / "site.key").chmod(0o600)
    load_config(p, environ={})
