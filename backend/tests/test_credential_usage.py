import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _env(clean_state):
    yield
    with main.conn() as db:
        for name in ("USE_ONE", "USE_TWO", "NOBODY_USES", "AI_KEY", "HIDDEN_KEY"): db.execute("DELETE FROM credentials WHERE name=?", (name,))
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM projects")
        db.execute("UPDATE workflows SET project_id=NULL")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def save(client, name, value="value-1234"):
    assert client.put(f"/api/credentials/{name}", json={"value": value}).status_code in (200, 201, 204)


def http_flow(client, name, cred):
    return make_flow(client, [node("t", "trigger"), node("h", "http", url=f"https://x.example/{{{{cred.{cred}}}}}", method="GET"), node("o", "output")], [edge("t", "h"), edge("h", "o")], name=name)


def usage(client):
    res = client.get("/api/credentials/usage")
    assert res.status_code == 200, res.text
    return res.json()


def test_each_credential_lists_the_workflows_using_it(client):
    for n in ("USE_ONE", "USE_TWO", "NOBODY_USES"): save(client, n)
    a, b = http_flow(client, "Uses one A", "USE_ONE"), http_flow(client, "Uses one B", "USE_ONE")
    http_flow(client, "Uses two", "USE_TWO")
    u = usage(client)
    assert {w["id"] for w in u["USE_ONE"]["used_by"]} == {a, b} and len(u["USE_TWO"]["used_by"]) == 1
    assert u["NOBODY_USES"] == {"used_by": [], "hidden": 0}  # nothing uses it, so it can be cleaned up


def test_a_credential_picked_in_a_field_counts_as_used(client):
    save(client, "AI_KEY", "http://127.0.0.1:11434/v1?model=m&key=sk-usage-field-1")
    wid = make_flow(client, [node("t", "trigger"), node("a", "ai", credential="AI_KEY", task="summarize", prompt="hi", length="short"), node("o", "output")], [edge("t", "a"), edge("a", "o")], name="Uses AI")
    assert [w["id"] for w in usage(client)["AI_KEY"]["used_by"]] == [wid]


def test_deleting_a_workflow_frees_the_credential(client):
    save(client, "USE_ONE")
    wid = http_flow(client, "Temporary user", "USE_ONE")
    assert usage(client)["USE_ONE"]["used_by"]
    client.delete(f"/api/workflows/{wid}")
    assert usage(client)["USE_ONE"]["used_by"] == []


def test_no_credential_value_is_returned(client):
    save(client, "USE_ONE", "value-never-shown-42")
    http_flow(client, "Keyed", "USE_ONE")
    assert "value-never-shown-42" not in client.get("/api/credentials/usage").text


def test_usage_needs_a_login(anon):
    assert anon.get("/api/credentials/usage").status_code == 401


def test_the_usage_route_does_not_get_mistaken_for_a_credential_name(client):
    assert client.get("/api/credentials").status_code == 200
    assert "usage" not in [c["name"] for c in client.get("/api/credentials").json()]


def test_people_do_not_see_credentials_or_workflows_outside_their_projects(client):
    a = client.post("/api/projects", json={"name": "CredAlpha"}).json()["id"]
    b = client.post("/api/projects", json={"name": "CredBeta"}).json()["id"]
    uid = client.post("/api/users", json={"username": "creduser", "password": "long-enough-pw", "role": "editor"}).json()["id"]
    client.put(f"/api/projects/{a}", json={"user_ids": [uid]})
    save(client, "USE_ONE")
    save(client, "HIDDEN_KEY")
    client.put("/api/credentials/HIDDEN_KEY/project", json={"project_id": b})
    http_flow(client, "Plain user", "USE_ONE")
    hidden = client.post("/api/workflows", params={"project_id": b}, json={"name": "Beta only", "description": "", "nodes": [node("t", "trigger"), node("h", "http", url="https://x.example/{{cred.USE_ONE}}", method="GET"), node("o", "output")], "edges": [edge("t", "h"), edge("h", "o")]}).json()["id"]
    me = TestClient(app)
    assert me.post("/api/auth/login", json={"username": "creduser", "password": "long-enough-pw"}).status_code == 200
    u = me.get("/api/credentials/usage").json()
    assert "HIDDEN_KEY" not in u  # a credential of another project is invisible
    assert hidden not in str(u) and "Beta only" not in str(u) and u["USE_ONE"]["hidden"] == 1  # a workflow of another project is counted, not named
    assert [w["name"] for w in u["USE_ONE"]["used_by"]] == ["Plain user"]
