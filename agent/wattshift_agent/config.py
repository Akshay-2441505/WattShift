"""Agent configuration: one YAML file the operator edits once (spec section 5)."""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

MODES = ("shadow", "autonomous")
MATCH_KEYS = ("qos", "partition", "name", "user", "account")
TOP_KEYS = {"cloud", "mode", "poll_seconds", "state_file", "audit_file", "slurm", "rules", "default"}
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")  # the only places a plain-http cloud address is acceptable
_DURATION = re.compile(r"^\s*(\d+)\s*([mhd])\s*$")
_UNIT_MIN = {"m": 1, "h": 60, "d": 1440}


class ConfigError(ValueError):
    """The config file is wrong; the message says what to fix."""


def parse_minutes(text) -> int:
    m = _DURATION.match(str(text))
    if not m:
        raise ConfigError(f"bad duration {text!r}: write it like 90m, 12h or 2d")
    return int(m.group(1)) * _UNIT_MIN[m.group(2)]


@dataclass(frozen=True)
class Rule:
    exact: tuple  # (("qos", "flex"), ...): fields that must be equal
    name_re: "re.Pattern | None"  # `name` is a regular expression, searched anywhere in the job name
    max_wait_min: int  # 0 = never touch jobs that match this rule


@dataclass(frozen=True)
class Config:
    cloud_url: str
    site_key: str
    mode: str
    rules: tuple
    state_path: Path
    audit_path: Path
    poll_seconds: int = 30
    slurm_prefix: tuple = ()  # e.g. ("ssh", "headnode"): how to reach Slurm when it is not on this machine


def _rule(i: int, raw) -> Rule:
    where = f"rules[{i}]"
    if not isinstance(raw, dict) or set(raw) != {"match", "max_wait"}:
        raise ConfigError(f"{where} must have exactly two keys: match and max_wait")
    match = raw["match"]
    if not isinstance(match, dict) or not match:
        raise ConfigError(f"{where}.match must list at least one field ({', '.join(MATCH_KEYS)})")
    bad = set(match) - set(MATCH_KEYS)
    if bad:
        raise ConfigError(f"{where}.match has unknown field(s) {sorted(bad)}; allowed: {', '.join(MATCH_KEYS)}")
    name_re = None
    if "name" in match:
        try:
            name_re = re.compile(str(match["name"]))
        except re.error as e:
            raise ConfigError(f"{where}.match.name is not a valid regular expression: {e}")
    exact = tuple(sorted((k, str(v)) for k, v in match.items() if k != "name"))
    return Rule(exact, name_re, parse_minutes(raw["max_wait"]))


def load_config(path, environ=None) -> Config:
    env = os.environ if environ is None else environ
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}")
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}")
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain settings, not {type(raw).__name__}")
    unknown = set(raw) - TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown setting(s): {', '.join(sorted(unknown))}")

    cloud = raw.get("cloud") or {}
    if not isinstance(cloud, dict) or set(cloud) - {"url", "key_file"}:
        raise ConfigError("cloud takes only url and key_file (the site key never goes in this file: use key_file or WATTSHIFT_SITE_KEY)")
    url = str(cloud.get("url", "")).rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ConfigError("cloud.url must start with http:// or https://")
    if url.startswith("http://") and (urlparse(url).hostname or "") not in LOCAL_HOSTS:
        # The site key travels in a request header: over plain http anyone on the path could read it and report as this site.
        raise ConfigError("cloud.url must use https:// (plain http:// would send the site key unencrypted); http:// is only allowed for localhost")
    key = env.get("WATTSHIFT_SITE_KEY", "").strip()
    if not key and cloud.get("key_file"):
        key_path = Path(cloud["key_file"]).expanduser()
        try:
            key = key_path.read_text(encoding="utf-8").strip()
            loose = os.name != "nt" and key_path.stat().st_mode & 0o077
        except OSError as e:
            raise ConfigError(f"cannot read cloud.key_file: {e}")
        if loose:
            raise ConfigError(f"cloud.key_file {key_path} can be read by other users: run  chmod 600 {key_path}")
    if not key:
        raise ConfigError("no site key: set WATTSHIFT_SITE_KEY or cloud.key_file")

    mode = raw.get("mode", "shadow")
    if mode not in MODES:
        raise ConfigError(f"mode must be one of {MODES}, not {mode!r}")
    poll = raw.get("poll_seconds", 30)
    if not isinstance(poll, int) or isinstance(poll, bool) or not 10 <= poll <= 300:
        raise ConfigError("poll_seconds must be a whole number from 10 to 300")
    if raw.get("default", "none") != "none":
        raise ConfigError("default must be none: a job that matches no rule is never touched")
    slurm = raw.get("slurm") or {}
    if not isinstance(slurm, dict) or set(slurm) - {"prefix"}:
        raise ConfigError("slurm takes only prefix")
    prefix = slurm.get("prefix") or []
    if not isinstance(prefix, list) or not all(isinstance(p, str) for p in prefix):
        raise ConfigError("slurm.prefix must be a list of words")
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise ConfigError("rules must be a list")

    here = path.resolve().parent

    def where(name: str, default: str) -> Path:
        p = Path(raw.get(name, default)).expanduser()
        return p if p.is_absolute() else here / p

    return Config(
        cloud_url=url, site_key=key, mode=mode, rules=tuple(_rule(i, r) for i, r in enumerate(rules_raw)),
        state_path=where("state_file", "state.db"), audit_path=where("audit_file", "audit.log"),
        poll_seconds=poll, slurm_prefix=tuple(prefix),
    )
