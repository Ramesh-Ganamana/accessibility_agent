import threading

import pytest

from demo_app.server import make_server


@pytest.fixture
def demo():
    server = make_server("fixture@example.test", "fixture-only-password", extra=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/", server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
