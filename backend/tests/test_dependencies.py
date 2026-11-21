import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _env(clean_state):
    with main.conn() as db: db.execute("DELETE FROM settings WHERE key='default_error_workflow'")
    yield
    with main.conn() as db:
        db.execute("DELETE FROM settings WHERE key='default_error_workflow'")
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM projects")
        db.execute("UPDATE workflows SET project_id=NULL")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def plain(client, name):
    return make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name=name)


def deps(client, wid):
    return client.get(f"/api/workflows/{wid}/dependencies")


def test_a_sub_workflow_shows_who_calls_it_and_the_caller_shows_what_it_calls(client):
    child = plain(client, "Child")
    parent = make_flow(client, [node("t", "trigger"), node("s", "subflow", workflow_id=child), node("o", "output")], [edge("t", "s"), edge("s", "o")], name="Parent")
    assert [(c["id"], c["via"], c["name"]) for c in deps(client, parent).json()["calls"]] == [(child, "subflow", "Child")]
    assert [(c["id"], c["via"]) for c in deps(client, child).json()["called_by"]] == [(parent, "subflow")]
    assert deps(client, parent).json()["called_by"] == []


def test_agent_tools_and_error_handlers_count_as_references(client):
    tool, handler = plain(client, "Tool flow"), plain(client, "Handler")
    wid = make_flow(client, [node("t", "trigger"), node("a", "agent", workflows=f"{tool}, ", credential="LLM"), node("h", "set", json="{}", error_workflow=handler), node("o", "output")],
                    [edge("t", "a"), edge("a", "h"), edge("h", "o")], name="Uses both")
    assert {(c["id"], c["via"]) for c in deps(client, wid).json()["calls"]} == {(tool, "agent"), (handler, "error_handler")}
    assert deps(client, handler).json()["called_by"][0]["via"] == "error_handler"


def test_the_shared_error_workflow_is_flagged(client):
    handler = plain(client, "Shared handler")
    assert deps(client, handler).json()["is_default_error_handler"] is False
    client.put("/api/settings/error-workflow", json={"workflow_id": handler})
    assert deps(client, handler).json()["is_default_error_handler"] is True
    assert next(w for w in client.get("/api/dependencies").json()["workflows"] if w["id"] == handler)["is_default_error_handler"] is True
    assert client.get("/api/dependencies").json()["default_error_workflow"] == handler


def test_a_reference_to_a_deleted_workflow_is_reported_as_missing(client):
    gone = plain(client, "Soon gone")
    parent = make_flow(client, [node("t", "trigger"), node("s", "subflow", workflow_id=gone), node("o", "output")], [edge("t", "s"), edge("s", "o")], name="Dangling")
    client.delete(f"/api/workflows/{gone}")
    info = deps(client, parent).json()
    assert info["calls"] == [] and info["missing_workflows"] == [gone]


def test_credentials_are_listed_and_missing_ones_are_flagged(client):
    client.put("/api/credentials/HAVE_KEY", json={"value": "value-1234"})
    wid = make_flow(client, [node("t", "trigger"), node("h", "http", url="https://x.example/{{cred.HAVE_KEY}}/{{cred.LOST_KEY}}", method="GET"), node("o", "output")], [edge("t", "h"), edge("h", "o")], name="Needs keys")
    info = deps(client, wid).json()
    assert info["credentials"] == ["HAVE_KEY", "LOST_KEY"] and info["missing_credentials"] == ["LOST_KEY"]


def test_no_credential_value_is_ever_returned(client):
    client.put("/api/credentials/HAVE_KEY", json={"value": "value-never-shown-9"})
    wid = make_flow(client, [node("t", "trigger"), node("h", "http", url="https://x.example/{{cred.HAVE_KEY}}", method="GET"), node("o", "output")], [edge("t", "h"), edge("h", "o")], name="Keyed")
    assert "value-never-shown-9" not in deps(client, wid).text and "value-never-shown-9" not in client.get("/api/dependencies").text


def test_the_full_map_lists_every_workflow_once(client):
    a, b = plain(client, "Map A"), plain(client, "Map B")
    ids = [w["id"] for w in client.get("/api/dependencies").json()["workflows"]]
    assert a in ids and b in ids and len(ids) == len(set(ids))


def test_unknown_workflow_and_logged_out_callers(client, anon):
    assert deps(client, "no-such-flow").status_code == 404
    assert anon.get("/api/dependencies").status_code == 401
    assert anon.get("/api/workflows/x/dependencies").status_code == 401


def test_people_only_see_references_to_workflows_they_can_see(client):
    a = client.post("/api/projects", json={"name": "DepAlpha"}).json()["id"]
    b = client.post("/api/projects", json={"name": "DepBeta"}).json()["id"]
    uid = client.post("/api/users", json={"username": "depuser", "password": "long-enough-pw", "role": "editor"}).json()["id"]
    client.put(f"/api/projects/{a}", json={"user_ids": [uid]})
    secret = client.post("/api/workflows", params={"project_id": b}, json={"name": "Hidden child", "description": "", "nodes": [node("t", "trigger"), node("o", "output")], "edges": [edge("t", "o")]}).json()["id"]
    mine = client.post("/api/workflows", params={"project_id": a}, json={"name": "Visible parent", "description": "", "nodes": [node("t", "trigger"), node("s", "subflow", workflow_id=secret), node("o", "output")], "edges": [edge("t", "s"), edge("s", "o")]}).json()["id"]
    me = TestClient(app)
    assert me.post("/api/auth/login", json={"username": "depuser", "password": "long-enough-pw"}).status_code == 200
    info = deps(me, mine).json()
    assert info["hidden_references"] == 1 and info["missing_workflows"] == [] and secret not in str(info)
    assert "Hidden child" not in str(info) and "Hidden child" not in me.get("/api/dependencies").text
    assert deps(me, secret).status_code == 404
