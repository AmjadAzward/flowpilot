import os
import tempfile
from pathlib import Path

# Point the app at a throwaway database before it is imported.
_tmp = tempfile.mkdtemp(prefix="flowpilot-test-")
os.environ["FLOWPILOT_DB"] = str(Path(_tmp) / "test.db")

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
