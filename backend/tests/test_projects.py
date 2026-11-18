import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM projects")
        db.execute("UPDATE workflows SET project_id=NULL")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def add_user(owner, name, role):
    res = owner.post("/api/users", json={"username": name, "password": "long-enough-pw", "role": role})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def login(name):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": name, "password": "long-enough-pw"}).status_code == 200
    return c


def flow(client, name, project=None, extra_nodes=None, extra_edges=None):
    nodes = [node("t", "trigger"), node("o", "output")] + (extra_nodes or [])
    res = client.post("/api/workflows", params={"project_id": project} if project else None,
                      json={"name": name, "description": "", "nodes": nodes, "edges": [edge("t", "o")] + (extra_edges or [])})
    assert res.status_code == 201, res.text
    return res.json()["id"]


@pytest.fixture()
def world(client):
    """Two projects, an editor in each, one outsider editor, and a viewer in project A."""
    a = client.post("/api/projects", json={"name": "Alpha"}).json()["id"]
    b = client.post("/api/projects", json={"name": "Beta"}).json()["id"]
    ids = {n: add_user(client, n, role) for n, role in (("ann", "editor"), ("bob", "editor"), ("cat", "editor"), ("vic", "viewer"))}
    client.put(f"/api/projects/{a}", json={"user_ids": [ids["ann"], ids["vic"]]})
    client.put(f"/api/projects/{b}", json={"user_ids": [ids["bob"]]})
    w = {"shared": flow(client, "Shared"), "alpha": flow(client, "Alpha flow", a), "beta": flow(client, "Beta flow", b)}
    return {"a": a, "b": b, "ids": ids, "w": w, "ann": login("ann"), "bob": login("bob"), "cat": login("cat"), "vic": login("vic")}


def names(c):
    return sorted(w["name"] for w in c.get("/api/workflows").json())


# ---------------------------------------------------------------- visibility
def test_members_see_their_projects_plus_unassigned_and_admins_see_all(client, world):
    assert names(client) and {"Shared", "Alpha flow", "Beta flow"} <= set(names(client))
    assert [n for n in names(world["ann"]) if n in ("Shared", "Alpha flow", "Beta flow")] == ["Alpha flow", "Shared"]
    assert [n for n in names(world["bob"]) if n in ("Shared", "Alpha flow", "Beta flow")] == ["Beta flow", "Shared"]
    assert [n for n in names(world["cat"]) if n in ("Shared", "Alpha flow", "Beta flow")] == ["Shared"]
    assert [n for n in names(world["vic"]) if n in ("Shared", "Alpha flow", "Beta flow")] == ["Alpha flow", "Shared"]


def test_workflows_in_other_projects_look_like_they_do_not_exist(client, world):
    wid = world["w"]["beta"]
    ann = world["ann"]
    for method, path, body in (("GET", f"/api/workflows/{wid}", None), ("PUT", f"/api/workflows/{wid}", {"name": "x", "description": "", "nodes": [], "edges": []}),
                               ("DELETE", f"/api/workflows/{wid}", None), ("POST", f"/api/workflows/{wid}/run", None), ("POST", f"/api/workflows/{wid}/duplicate", None),
                               ("POST", f"/api/workflows/{wid}/active", {"active": True}), ("GET", f"/api/workflows/{wid}/versions", None),
                               ("POST", f"/api/workflows/{wid}/webhook/rotate", None), ("POST", f"/api/workflows/{wid}/versions/1/restore", None)):
        res = ann.request(method, path, json=body)
        assert res.status_code == 404, (method, path, res.status_code)
    assert ann.get(f"/api/workflows/{world['w']['alpha']}").status_code == 200
    assert client.get(f"/api/workflows/{wid}").status_code == 200  # the owner is unaffected


def test_members_can_work_inside_their_project_but_viewers_stay_read_only(client, world):
    wid = world["w"]["alpha"]
    assert world["ann"].post(f"/api/workflows/{wid}/run").json()["status"] == "success"
    assert world["ann"].put(f"/api/workflows/{wid}", json={"name": "Alpha renamed", "description": "", "nodes": [node("t", "trigger")], "edges": []}).status_code == 200
    assert world["vic"].get(f"/api/workflows/{wid}").status_code == 200
    assert world["vic"].post(f"/api/workflows/{wid}/run").status_code == 403


def test_unassigned_workflows_stay_open_to_everyone(client, world):
    wid = world["w"]["shared"]
    assert world["cat"].get(f"/api/workflows/{wid}").status_code == 200
    assert world["cat"].post(f"/api/workflows/{wid}/run").json()["status"] == "success"


# ---------------------------------------------------------------- runs and history
def test_history_is_filtered_and_cleared_per_visibility(client, world):
    for key in ("alpha", "beta", "shared"): client.post(f"/api/workflows/{world['w'][key]}/run")
    ids_seen = lambda c, **q: {r["workflow_id"] for r in c.get("/api/executions", params=q).json()}
    assert ids_seen(world["ann"]) == {world["w"]["alpha"], world["w"]["shared"]}
    assert ids_seen(world["cat"]) == {world["w"]["shared"]}
    assert world["ann"].get("/api/executions", params={"workflow_id": world["w"]["beta"]}).json() == []
    assert world["ann"].delete("/api/executions").status_code == 204
    assert ids_seen(client) == {world["w"]["beta"]}  # only what ann could see was cleared
    client.post(f"/api/workflows/{world['w']['alpha']}/run")
    assert world["ann"].delete("/api/executions", params={"workflow_id": world["w"]["beta"]}).status_code == 204
    assert world["w"]["beta"] in ids_seen(client)


def test_cancelling_someone_elses_waiting_run_is_blocked(client, world):
    wid = flow(client, "Waits", world["b"], [node("w", "wait", seconds="1", unit="hours")], [edge("t", "w")])
    run = client.post(f"/api/workflows/{wid}/run").json()
    assert run["status"] == "waiting"
    assert world["ann"].post(f"/api/executions/{run['id']}/cancel").status_code == 404
    assert world["bob"].post(f"/api/executions/{run['id']}/cancel").status_code == 200


# ---------------------------------------------------------------- creating and moving
def test_creating_in_a_project_needs_membership(client, world):
    assert world["ann"].post("/api/workflows", params={"project_id": world["a"]}, json={"name": "mine", "description": "", "nodes": [], "edges": []}).status_code == 201
    assert world["ann"].post("/api/workflows", params={"project_id": world["b"]}, json={"name": "no", "description": "", "nodes": [], "edges": []}).status_code == 403
    assert world["ann"].post("/api/workflows", params={"project_id": "nope"}, json={"name": "no", "description": "", "nodes": [], "edges": []}).status_code == 404
    mine = next(w for w in world["ann"].get("/api/workflows").json() if w["name"] == "mine")
    assert mine["project_name"] == "Alpha" and world["bob"].get(f"/api/workflows/{mine['id']}").status_code == 404


def test_only_the_owner_manages_projects_and_assignment(client, world):
    for c in (world["ann"], world["vic"]):
        assert c.post("/api/projects", json={"name": "Hack"}).status_code == 403
        assert c.put(f"/api/projects/{world['a']}", json={"user_ids": []}).status_code == 403
        assert c.put(f"/api/projects/assign/{world['w']['alpha']}", json={"project_id": None}).status_code == 403
        assert c.delete(f"/api/projects/{world['a']}").status_code == 403
    client.put(f"/api/projects/assign/{world['w']['shared']}", json={"project_id": world["b"]})
    assert world["cat"].get(f"/api/workflows/{world['w']['shared']}").status_code == 404
    client.put(f"/api/projects/assign/{world['w']['shared']}", json={"project_id": None})
    assert world["cat"].get(f"/api/workflows/{world['w']['shared']}").status_code == 200


def test_duplicate_stays_in_the_same_project(client, world):
    copy = world["ann"].post(f"/api/workflows/{world['w']['alpha']}/duplicate").json()["id"]
    assert world["bob"].get(f"/api/workflows/{copy}").status_code == 404
    assert next(w for w in client.get("/api/workflows").json() if w["id"] == copy)["project_name"] == "Alpha"


# ---------------------------------------------------------------- run workflow node
def test_a_member_cannot_wire_in_a_workflow_they_cannot_see(client, world):
    nodes = [node("t", "trigger"), node("s", "subflow", workflow_id=world["w"]["beta"])]
    res = world["ann"].post("/api/workflows", json={"name": "sneaky", "description": "", "nodes": nodes, "edges": [edge("t", "s")]})
    assert res.status_code == 403
    ok = world["ann"].post("/api/workflows", json={"name": "fine", "description": "", "nodes": [node("t", "trigger"), node("s", "subflow", workflow_id=world["w"]["shared"])], "edges": [edge("t", "s")]})
    assert ok.status_code == 201
    mine = world["ann"].post("/api/workflows", json={"name": "t", "description": "", "nodes": [node("t", "trigger")], "edges": []}).json()["id"]
    upd = world["ann"].put(f"/api/workflows/{mine}", json={"name": "t", "description": "", "nodes": nodes, "edges": [edge("t", "s")]})
    assert upd.status_code == 403


# ---------------------------------------------------------------- project management
def test_project_crud_rules(client, world):
    assert client.post("/api/projects", json={"name": "alpha"}).status_code == 409  # names are unique, case-insensitive
    assert client.put(f"/api/projects/{world['b']}", json={"name": "ALPHA"}).status_code == 409
    assert client.put(f"/api/projects/{world['b']}", json={"name": "Beta 2"}).status_code == 200
    assert client.put(f"/api/projects/{world['b']}", json={"user_ids": ["no-such-user"]}).status_code == 400
    assert client.delete(f"/api/projects/{world['a']}").status_code == 400  # still has a workflow
    client.put(f"/api/projects/assign/{world['w']['alpha']}", json={"project_id": None})
    assert client.delete(f"/api/projects/{world['a']}").status_code == 204
    assert client.put("/api/projects/nope", json={"name": "x"}).status_code == 404


def test_listing_shows_members_only_to_admins(client, world):
    full = {p["name"]: p for p in client.get("/api/projects").json()}
    assert sorted(m["username"] for m in full["Alpha"]["members"]) == ["ann", "vic"] and full["Alpha"]["workflows"] == 1
    mine = world["ann"].get("/api/projects").json()
    assert [p["name"] for p in mine] == ["Alpha"] and "members" not in mine[0]
    assert world["cat"].get("/api/projects").json() == []


def test_status_lists_my_projects_and_deleting_a_user_drops_membership(client, world):
    assert [p["name"] for p in world["ann"].get("/api/auth/status").json()["projects"]] == ["Alpha"]
    assert {p["name"] for p in client.get("/api/auth/status").json()["projects"]} == {"Alpha", "Beta"}
    client.delete(f"/api/users/{world['ids']['ann']}")
    with main.conn() as db:
        assert db.execute("SELECT COUNT(*) c FROM project_members WHERE user_id=?", (world["ids"]["ann"],)).fetchone()["c"] == 0


def test_webhooks_and_schedules_ignore_projects(client, anon, world):
    nodes = [node("w", "webhook"), node("o", "output")]
    wid = world["bob"].post("/api/workflows", params={"project_id": world["b"]}, json={"name": "hook", "description": "", "nodes": nodes, "edges": [edge("w", "o")]}).json()["id"]
    world["bob"].post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    assert anon.post(f"/api/hooks/{token}", json={}).json()["status"] == "success"


def test_existing_databases_gain_the_project_column():
    with main.conn() as db:
        assert "project_id" in {r["name"] for r in db.execute("PRAGMA table_info(workflows)")}


def test_export_and_replay_respect_project_visibility(client, world):
    import csv, io
    for key in ("alpha", "beta", "shared"): client.post(f"/api/workflows/{world['w'][key]}/run")
    seen = lambda c: {r["workflow"] for r in csv.DictReader(io.StringIO(c.get("/api/executions/export").text))}
    assert {"Alpha flow", "Shared"} <= seen(world["ann"]) and "Beta flow" not in seen(world["ann"])
    assert seen(world["cat"]) >= {"Shared"} and not ({"Alpha flow", "Beta flow"} & seen(world["cat"]))
    failing = flow(client, "Beta failing", world["b"], [node("f", "fail", message="x")], [edge("t", "f")])
    run = client.post(f"/api/workflows/{failing}/run").json()
    assert run["status"] == "failed"
    assert world["ann"].post(f"/api/executions/{run['id']}/replay").status_code == 404  # another project's run looks like it does not exist
    assert world["bob"].post(f"/api/executions/{run['id']}/replay").status_code == 200
