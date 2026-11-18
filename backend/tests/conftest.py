import os
import tempfile
from pathlib import Path

# Point the app at a throwaway database before it is imported.
_tmp = tempfile.mkdtemp(prefix="flowpilot-test-")
os.environ["FLOWPILOT_DB"] = str(Path(_tmp) / "test.db")
os.environ["FLOWPILOT_SCHEDULER"] = "0"  # tests drive scheduler_tick() directly

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


OWNER = {"username": "owner", "password": "correct-horse-battery"}


@pytest.fixture(scope="session")
def session_cookie():
    """Create the owner account once and return its session cookie."""
    c = TestClient(app)
    res = c.post("/api/auth/setup", json=OWNER)
    assert res.status_code == 201, res.text
    return res.cookies["fp_session"]


@pytest.fixture()
def client(session_cookie):
    """A client that is already signed in as the owner."""
    c = TestClient(app)
    c.cookies.set("fp_session", session_cookie)
    return c


@pytest.fixture()
def anon():
    """A client with no session."""
    return TestClient(app)


def node(nid, typ, **data):
    return {"id": nid, "type": typ, "position": {"x": 0, "y": 0}, "data": {"label": nid, **data}}


def edge(src, dst, handle=None):
    e = {"id": f"{src}-{dst}", "source": src, "target": dst}
    if handle:
        e["sourceHandle"] = handle
    return e


def make_flow(client, nodes, edges, name="t"):
    res = client.post("/api/workflows", json={"name": name, "description": "", "nodes": nodes, "edges": edges})
    assert res.status_code == 201, res.text
    return res.json()["id"]


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text, self.url = status, payload, text, ""
        self.content = (str(payload) if payload is not None else text).encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code} for url {self.url}", request=httpx.Request("GET", "https://x"), response=httpx.Response(self.status_code))


@pytest.fixture()
def fake_http(monkeypatch):
    """Replace the HTTP client so no real network call happens. `safe_url` is also bypassed (it has its own tests)."""
    from app import main
    calls, inits = [], []
    reply = {"response": FakeResponse(200, {"ok": True})}

    class FakeClient:
        def __init__(self, **kwargs): inits.append(kwargs)
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def request(self, method, url, headers=None, **kwargs):
            calls.append({"method": method, "url": url, "headers": headers, **kwargs})
            reply["response"].url = url
            return reply["response"]

    monkeypatch.setattr(main.httpx, "Client", FakeClient)
    monkeypatch.setattr(main, "safe_url", lambda u: u)
    return type("Fake", (), {"calls": calls, "inits": inits, "reply": reply})


@pytest.fixture()
def clean_state(session_cookie):
    """Remove credentials and extra workflows before and after a test."""
    from app import main

    def wipe():
        with main.conn() as db:
            db.execute("DELETE FROM credentials")
            db.execute("DELETE FROM workflows WHERE id != 'demo-workflow'")
            db.execute("DELETE FROM executions")
    wipe()
    yield
    wipe()
