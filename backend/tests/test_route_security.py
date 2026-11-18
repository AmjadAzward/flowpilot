"""Walks every registered API route and checks who may reach it. New routes are covered automatically, so a
forgotten permission check fails here instead of shipping."""
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import main
from app.main import app

PUBLIC = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/auth/logout", "/api/oauth/callback", "/api/hooks/{token}"}
OWNER_ONLY_PREFIXES = ("/api/users", "/api/backup", "/api/audit")
ADMIN_WRITE_PREFIXES = ("/api/credentials", "/api/oauth", "/api/settings")
SKIP_METHODS = {"HEAD", "OPTIONS"}


def routes():
    out = []
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api/"):
            for m in sorted(r.methods - SKIP_METHODS):
                out.append((m, r.path))
    return out


def concrete(path):
    return re.sub(r"\{[^}]+\}", "x", path)


@pytest.fixture(scope="module")
def clients(session_cookie):
    owner = TestClient(app)
    owner.cookies.set("fp_session", session_cookie)
    for name, role in (("sec-viewer", "viewer"), ("sec-editor", "editor"), ("sec-admin", "admin")):
        owner.post("/api/users", json={"username": name, "password": "long-enough-pw", "role": role})
    made = {}
    for name in ("viewer", "editor", "admin"):
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"username": f"sec-{name}", "password": "long-enough-pw"}).status_code == 200
        made[name] = c
    yield made
    with main.conn() as db:
        db.execute("DELETE FROM users WHERE username LIKE 'sec-%'")


def test_the_route_list_is_not_empty():
    assert len(routes()) > 25


@pytest.mark.parametrize("method,path", routes())
def test_signed_out_requests_are_rejected_unless_the_route_is_public(method, path):
    res = TestClient(app).request(method, concrete(path), json={} if method in ("POST", "PUT", "PATCH") else None)
    if path in PUBLIC:
        assert res.status_code != 401, (method, path)
    else:
        assert res.status_code == 401, (method, path, res.status_code)


@pytest.mark.parametrize("method,path", [r for r in routes() if r[1] not in PUBLIC])
def test_role_matrix(clients, method, path):
    body = {} if method in ("POST", "PUT", "PATCH") else None

    def status(role):
        return clients[role].request(method, concrete(path), json=body).status_code

    write = method not in ("GET", "HEAD")
    if path.startswith(OWNER_ONLY_PREFIXES):
        for role in ("viewer", "editor", "admin"): assert status(role) == 403, (role, method, path)
    elif write and path.startswith("/api/projects"):
        for role in ("viewer", "editor", "admin"): assert status(role) == 403, (role, method, path)
    elif path == "/api/auth/password":
        for role in ("viewer", "editor", "admin"): assert status(role) != 403, (role, method, path)
    elif write and path.startswith(ADMIN_WRITE_PREFIXES):
        assert status("viewer") == 403 and status("editor") == 403, (method, path)
        assert status("admin") != 403, (method, path)
    elif write:
        assert status("viewer") == 403, (method, path)
        assert status("editor") != 403 and status("admin") != 403, (method, path)
    else:
        for role in ("viewer", "editor", "admin"): assert status(role) != 403, (role, method, path)


def test_public_webhook_and_callback_need_no_session_but_reveal_nothing(anon):
    assert anon.post("/api/hooks/not-a-real-token", json={}).status_code == 404
    loc = anon.get("/api/oauth/callback", params={"code": "x", "state": "nope"}, follow_redirects=False)
    assert loc.status_code == 303 and "oauth=error" in loc.headers["location"]


def test_responses_never_contain_password_hashes_or_secrets(client):
    client.put("/api/credentials/SEC_TEST", json={"value": "supersecretvalue-xyz"})
    for path in ("/api/users", "/api/credentials", "/api/workflows", "/api/executions", "/api/auth/status", "/api/oauth/info"):
        text = client.get(path).text
        assert "pbkdf2" not in text and "supersecretvalue-xyz" not in text, path
    client.delete("/api/credentials/SEC_TEST")
