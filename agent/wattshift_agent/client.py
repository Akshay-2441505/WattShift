"""The agent's only outbound connection: one JSON POST to the Wattshift cloud (stdlib only)."""
import json
import urllib.error
import urllib.request


class CloudError(Exception):
    """The cloud could not be reached, or answered with an error."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the request carries the site key, and it must only ever go where the operator configured."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
MAX_REPLY_BYTES = 5_000_000  # a real reply is a few kilobytes; a hostile one must not fill the agent's memory


class Cloud:
    def __init__(self, url: str, key: str, timeout: int = 20):
        self.url, self.key, self.timeout = url.rstrip("/"), key, timeout

    def _open(self, req):
        try:
            with _OPENER.open(req, timeout=self.timeout) as r:
                return r.read(MAX_REPLY_BYTES)
        except urllib.error.HTTPError as e:
            raise CloudError(f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise CloudError(str(e))

    def post_sync(self, body: dict) -> dict:
        req = urllib.request.Request(
            f"{self.url}/agent/v1/sync", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Site-Key": self.key},
        )
        raw = self._open(req)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise CloudError(f"the cloud's reply was not JSON: {e}")

    def health(self) -> None:
        self._open(urllib.request.Request(f"{self.url}/health", method="GET"))
