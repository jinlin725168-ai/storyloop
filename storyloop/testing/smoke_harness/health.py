"""Explicit read-only health probes; no authentication or implicit HTTP-error readiness."""
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from ...models import StoryLoopError, require


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        return None


def wait_http(url: str, expected_status: int = 200, timeout: float = 5) -> None:
    """Poll a credential-free GET endpoint for the exact declared 2xx status."""
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment,
            "Health probe requires a credential-free read-only HTTP URL")
    require(type(expected_status) is int and 200 <= expected_status < 300
            and type(timeout) in {int, float} and timeout > 0, "Invalid health probe expectation")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(urllib.request.Request(url, method="GET"),
                             timeout=max(.01, min(1, deadline - time.monotonic()))) as response:
                if response.status == expected_status:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(min(.05, max(0, deadline - time.monotonic())))
    raise StoryLoopError("Health probe did not reach the declared success status")


def free_port() -> int:
    """Return an advisory loopback port; the service must still handle binding races."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]
