import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from wattshift_agent.client import Cloud, CloudError

SEEN = []


class Handler(BaseHTTPRequestHandler):
    reply = (200, b'{"decisions": [], "release_all": false, "next_poll_s": 30}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append((self.path, self.headers["X-Site-Key"], self.headers["Content-Type"], body))
        code, data = Handler.reply
        self.send_response(code)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    SEEN.clear()
    Handler.reply = (200, b'{"decisions": [], "release_all": false, "next_poll_s": 30}')
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_post_sync_sends_the_key_and_returns_the_reply(server):
    out = Cloud(server, "wsk_abc").post_sync({"hello": 1})
    assert out == {"decisions": [], "release_all": False, "next_poll_s": 30}
    assert SEEN == [("/agent/v1/sync", "wsk_abc", "application/json", {"hello": 1})]


def test_an_http_error_is_a_cloud_error_with_the_status(server):
    Handler.reply = (401, b'{"detail": "missing or invalid X-Site-Key"}')
    with pytest.raises(CloudError, match="401"):
        Cloud(server, "wsk_bad").post_sync({})


def test_a_reply_that_is_not_json_is_a_cloud_error(server):
    Handler.reply = (200, b"<html>oops</html>")
    with pytest.raises(CloudError):
        Cloud(server, "k").post_sync({})


def test_an_unreachable_cloud_is_a_cloud_error():
    with pytest.raises(CloudError):
        Cloud("http://127.0.0.1:9", "k", timeout=2).post_sync({})


def test_health(server):
    Cloud(server, "k").health()
    with pytest.raises(CloudError):
        Cloud("http://127.0.0.1:9", "k", timeout=2).health()


def test_a_redirect_is_never_followed_so_the_key_only_goes_where_it_was_configured(server):
    other = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=other.serve_forever, daemon=True).start()

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{other.server_port}/agent/v1/sync")
            self.end_headers()

        def log_message(self, *a):
            pass

    hop = HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=hop.serve_forever, daemon=True).start()
    try:
        with pytest.raises(CloudError, match="307"):
            Cloud(f"http://127.0.0.1:{hop.server_port}", "wsk_secret").post_sync({"jobs": []})
        assert SEEN == []  # the redirect target never saw the key
    finally:
        hop.shutdown()
        other.shutdown()
