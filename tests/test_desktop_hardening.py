import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from desktop_client.client import BootstrapState
from desktop_client.hardened_client import SecureBootstrapHandler


def test_local_bootstrap_rejects_normal_web_origins() -> None:
    state = BootstrapState()
    state.activate({"server_url": "https://example.test", "pairing_code": "secret"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), SecureBootstrapHandler)
    server.bootstrap_state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/bootstrap"
    try:
        request = urllib.request.Request(url, headers={"Origin": "https://evil.example"})
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("normal web origin should be rejected")
        except urllib.error.HTTPError as error:
            assert error.code == 403

        extension_origin = "chrome-extension://abcdefghijklmnop"
        request = urllib.request.Request(url, headers={"Origin": extension_origin})
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert response.headers["Access-Control-Allow-Origin"] == extension_origin
            assert json.loads(response.read())["pairing_code"] == "secret"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
