"""Local integration fixtures; production isolation is tested separately in Docker."""
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from http.server import ThreadingHTTPServer
from harness.env import Env
import sandbox.server as srv


@contextmanager
def environment():
    previous_root, previous_token = srv.WORKSPACE, srv._AUTH_TOKEN
    root = Path(__file__).resolve().parent.parent / ".workspace"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        srv.WORKSPACE = Path(directory)
        srv._AUTH_TOKEN = "local-test-only"
        server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = Env(f"http://127.0.0.1:{server.server_address[1]}")
        env.secret = srv._AUTH_TOKEN
        try:
            yield env
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            srv.WORKSPACE, srv._AUTH_TOKEN = previous_root, previous_token
