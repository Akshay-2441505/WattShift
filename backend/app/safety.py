"""Things a stranger must never read back out of the API or the logs."""
import re

_HOME_PATH = re.compile(r"(?:[A-Za-z]:\\Users\\|/home/|/Users/)[^\s'\"]+")
_TOKEN = re.compile(r"\b(?:wsk_[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{32,})\b")


def safe_reason(text: str | None, limit: int = 200) -> str | None:
    """A failure message with local file paths and anything that looks like a key or token taken out, and cut short.
    Provider errors quote command output, and that output can name the operator's home directory or credentials."""
    if text is None:
        return None
    text = _HOME_PATH.sub("[path]", text)
    text = _TOKEN.sub("[secret]", text)
    return text[:limit]
