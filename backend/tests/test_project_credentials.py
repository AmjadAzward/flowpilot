import json

import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM workflows WHERE id != ?", ("demo-workflow",))  # keep the seeded demo workflow other tests rely on
        db.execute("DELETE FROM projects")


def project(client, name):
    res = client.post("/api/projects", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def slack_flow(client, cred, project_id=None):
    n = node("a", "app", service="slack", operation="post_message", credential=cred, fields=json.dumps({"channel": "#x", "text": "hi"}))
    wid = make_flow(client, [node("t", "trigger"), n, node("o", "output")], [edge("t", "a"), edge("a", "o")])
    if project_id: assert client.put(f"/api/projects/assign/{wid}", json={"project_id": project_id}).status_code == 200
    return wid


def run(client, wid):
    return client.post(f"/api/workflows/{wid}/run").json()


def test_a_project_credential_works_only_in_that_project(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    a, b = project(client, "Team A"), project(client, "Team B")
    assert client.put("/api/credentials/TEAMKEY", json={"value": "xoxb-team-secret-1", "project_id": a}).status_code == 200
    assert run(client, slack_flow(client, "TEAMKEY", a))["status"] == "success"
    other = run(client, slack_flow(client, "TEAMKEY", b))
    assert other["status"] == "failed" and "Unknown credential" in other["error"]
    none = run(client, slack_flow(client, "TEAMKEY"))
    assert none["status"] == "failed" and "Unknown credential" in none["error"]
    assert "xoxb-team-secret-1" not in json.dumps([other, none])


def test_credentials_without_a_project_are_shared_as_before(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    a = project(client, "Team A")
    client.put("/api/credentials/SHARED", json={"value": "xoxb-shared-secret-1"})
    assert run(client, slack_flow(client, "SHARED", a))["status"] == "success"
    assert run(client, slack_flow(client, "SHARED"))["status"] == "success"


def test_scope_can_be_changed_without_retyping_the_value(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    a = project(client, "Team A")
    client.put("/api/credentials/MOVING", json={"value": "xoxb-moving-secret-1"})
    wid = slack_flow(client, "MOVING")
    assert run(client, wid)["status"] == "success"
    assert client.put("/api/credentials/MOVING/project", json={"project_id": a}).json() == {"name": "MOVING", "project_id": a}
    assert run(client, wid)["status"] == "failed"
    client.put(f"/api/projects/assign/{wid}", json={"project_id": a})
    assert run(client, wid)["status"] == "success"
    assert client.put("/api/credentials/MOVING/project", json={"project_id": None}).json()["project_id"] is None
    listed = {c["name"]: c for c in client.get("/api/credentials").json()}
    assert listed["MOVING"]["project_id"] is None


def test_saving_again_keeps_the_scope_unless_it_is_given(client):
    a = project(client, "Team A")
    client.put("/api/credentials/KEEP", json={"value": "first-secret-value", "project_id": a})
    client.put("/api/credentials/KEEP", json={"value": "second-secret-value"})
    row = next(c for c in client.get("/api/credentials").json() if c["name"] == "KEEP")
    assert row["project_id"] == a and row["project_name"] == "Team A"
    client.put("/api/credentials/KEEP", json={"value": "third-secret-value", "project_id": None})
    assert next(c for c in client.get("/api/credentials").json() if c["name"] == "KEEP")["project_id"] is None


def test_unknown_projects_are_refused(client):
    assert client.put("/api/credentials/NOPE", json={"value": "some-secret-value", "project_id": "missing"}).status_code == 404
    client.put("/api/credentials/REAL", json={"value": "some-secret-value"})
    assert client.put("/api/credentials/REAL/project", json={"project_id": "missing"}).status_code == 404
    assert client.put("/api/credentials/GHOST/project", json={"project_id": None}).status_code == 404
    assert not any(c["name"] == "NOPE" for c in client.get("/api/credentials").json())


def test_a_project_with_credentials_cannot_be_deleted(client):
    a = project(client, "Team A")
    client.put("/api/credentials/HELD", json={"value": "some-secret-value", "project_id": a})
    assert client.delete(f"/api/projects/{a}").status_code == 400
    client.put("/api/credentials/HELD/project", json={"project_id": None})
    assert client.delete(f"/api/projects/{a}").status_code == 204


def test_a_sub_workflow_runs_with_the_callers_credentials(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    a, b = project(client, "Team A"), project(client, "Team B")
    client.put("/api/credentials/ONLYA", json={"value": "xoxb-only-a-secret", "project_id": a})
    child = slack_flow(client, "ONLYA", a)

    def caller(pid):
        wid = make_flow(client, [node("t", "trigger"), node("s", "subflow", workflow_id=child), node("o", "output")], [edge("t", "s"), edge("s", "o")])
        client.put(f"/api/projects/assign/{wid}", json={"project_id": pid})
        return wid
    assert run(client, caller(a))["status"] == "success"
    blocked = run(client, caller(b))  # a workflow of another project cannot borrow A's credential by calling A's workflow
    assert blocked["status"] == "failed" and "xoxb-only-a-secret" not in json.dumps(blocked)


def test_only_admins_manage_credentials(client):
    a = project(client, "Team A")
    client.post("/api/users", json={"username": "cred-editor", "password": "long-enough-pw", "role": "editor"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "cred-editor", "password": "long-enough-pw"}).status_code == 200
    assert c.put("/api/credentials/X1/project", json={"project_id": a}).status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'cred-editor')}")
