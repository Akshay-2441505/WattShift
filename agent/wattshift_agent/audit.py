"""A rotating JSON-lines audit log: every Slurm command the agent runs and every decision it acts on."""
import json
import logging
import time
from logging.handlers import RotatingFileHandler


def make_audit_logger(path) -> logging.Logger:
    log = logging.getLogger(f"wattshift.audit.{path}")
    if not log.handlers:
        handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fmt = logging.Formatter("%(asctime)sZ %(message)s", "%Y-%m-%dT%H:%M:%S")
        fmt.converter = time.gmtime
        handler.setFormatter(fmt)
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def event(log, name: str, **fields) -> None:
    """One audit line; a no-op when there is no audit logger (unit tests)."""
    if log is not None:
        log.info(json.dumps({"event": name, **fields}, default=str))
