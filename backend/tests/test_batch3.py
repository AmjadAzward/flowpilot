import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM users WHERE role != 'owner'")
        db.execute("DELETE FROM workflow_versions")


def add_user(client, name, role, password="long-enough-pw"):
    res = client.post("/api/users", json={"username": name, "password": password, "role": role})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def login(name, password="long-enough-pw"):
    c = TestClient(app)
    res = c.post("/api/auth/login", json={"username": name, "password": password})
    assert res.status_code == 200, res.text
    return c, res.json()


# ---------------------------------------------------------------- users and roles
def test_owner_creates_lists_updates_and_deletes_users(client):
    uid = add_user(client, "erin", "editor")
    users = client.get("/api/users").json()
    assert {u["username"]: u["role"] for u in users}["erin"] == "editor"
    assert all("password" not in u and "password_hash" not in u for u in users)
    assert client.put(f"/api/users/{uid}", json={"role": "viewer"}).status_code == 200
    assert login("erin")[1]["role"] == "viewer"
    assert client.delete(f"/api/users/{uid}").status_code == 204
    assert TestClient(app).post("/api/auth/login", json={"username": "erin", "password": "long-enough-pw"}).status_code == 401


def test_user_validation_and_uniqueness(client):
    add_user(client, "dup", "editor")
    assert client.post("/api/users", json={"username": "dup", "password": "long-enough-pw", "role": "editor"}).status_code == 409
    assert client.post("/api/users", json={"username": "x y", "password": "long-enough-pw", "role": "editor"}).status_code == 422
    assert client.post("/api/users", json={"username": "shortpw", "password": "short", "role": "editor"}).status_code == 422
    assert client.post("/api/users", json={"username": "boss", "password": "long-enough-pw", "role": "owner"}).status_code == 400
    assert client.post("/api/users", json={"username": "boss", "password": "long-enough-pw", "role": "root"}).status_code == 400


def test_owner_cannot_be_changed_or_deleted(client):
    owner = next(u for u in client.get("/api/users").json() if u["role"] == "owner")
    assert client.delete(f"/api/users/{owner['id']}").status_code == 400
    assert client.put(f"/api/users/{owner['id']}", json={"role": "viewer"}).status_code == 400


def test_viewer_can_read_but_not_change_or_run(client):
    wid = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")])
    add_user(client, "vera", "viewer")
    v, info = login("vera")
    assert info["role"] == "viewer"
    assert v.get("/api/workflows").status_code == 200 and v.get(f"/api/workflows/{wid}").status_code == 200
    assert v.get("/api/executions").status_code == 200 and v.get("/api/credentials").status_code == 200
    body = {"name": "x", "description": "", "nodes": [], "edges": []}
    assert v.post("/api/workflows", json=body).status_code == 403
    assert v.put(f"/api/workflows/{wid}", json=body).status_code == 403
    assert v.delete(f"/api/workflows/{wid}").status_code == 403
    assert v.post(f"/api/workflows/{wid}/run").status_code == 403
    assert v.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 403
    assert v.delete("/api/executions").status_code == 403
    assert v.put("/api/credentials/ABCD", json={"value": "abcdefgh"}).status_code == 403
    assert v.get("/api/users").status_code == 403


def test_editor_builds_and_runs_but_cannot_touch_credentials_or_users(client):
    add_user(client, "eddie", "editor")
    e, _ = login("eddie")
    wid = make_flow(e, [node("t", "trigger"), node("o", "output")], [edge("t", "o")])
    assert e.post(f"/api/workflows/{wid}/run").json()["status"] == "success"
    assert e.put("/api/credentials/ABCD", json={"value": "abcdefgh"}).status_code == 403
    assert e.delete("/api/credentials/ABCD").status_code == 403
    assert e.post("/api/users", json={"username": "newbie", "password": "long-enough-pw", "role": "viewer"}).status_code == 403


def test_admin_manages_credentials_but_not_users(client):
    add_user(client, "ada", "admin")
    a, _ = login("ada")
    assert a.put("/api/credentials/ABCD", json={"value": "abcdefgh"}).status_code in (200, 201, 204)
    assert a.get("/api/users").status_code == 403


def test_deleting_or_demoting_takes_effect_immediately(client):
    uid = add_user(client, "temp", "editor")
    t, _ = login("temp")
    assert t.get("/api/workflows").status_code == 200
    client.put(f"/api/users/{uid}", json={"role": "viewer"})
    assert t.post("/api/workflows", json={"name": "x", "description": "", "nodes": [], "edges": []}).status_code == 403
    client.delete(f"/api/users/{uid}")
    assert t.get("/api/workflows").status_code == 401


def test_admin_reset_signs_the_user_out(client):
    uid = add_user(client, "pat", "editor")
    p, _ = login("pat")
    client.put(f"/api/users/{uid}", json={"password": "a-brand-new-password"})
    assert p.get("/api/workflows").status_code == 401
    login("pat", "a-brand-new-password")


def test_change_own_password(client):
    add_user(client, "cora", "viewer")
    c, _ = login("cora")
    other, _ = login("cora")
    assert c.post("/api/auth/password", json={"current": "wrong-password", "new": "another-good-one"}).status_code == 400
    assert c.post("/api/auth/password", json={"current": "long-enough-pw", "new": "short"}).status_code == 422
    assert c.post("/api/auth/password", json={"current": "long-enough-pw", "new": "another-good-one"}).status_code == 200
    assert c.get("/api/workflows").status_code == 200  # this session stays
    assert other.get("/api/workflows").status_code == 401  # other sessions are closed
    login("cora", "another-good-one")


def test_status_reports_role_and_unauthenticated_has_none(client, anon):
    assert client.get("/api/auth/status").json()["role"] == "owner"
    assert anon.get("/api/auth/status").json()["role"] is None
    assert anon.get("/api/users").status_code == 401


def test_webhooks_do_not_need_a_role(client, anon):
    wid = make_flow(client, [node("w", "webhook"), node("o", "output")], [edge("w", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    assert anon.post(f"/api/hooks/{token}", json={}).status_code == 200


def test_existing_installs_get_an_owner_on_migration():
    with main.conn() as db:
        cols = {r["name"] for r in db.execute("PRAGMA table_info(users)")}
        assert "role" in cols
        assert db.execute("SELECT COUNT(*) c FROM users WHERE role='owner'").fetchone()["c"] == 1


# ---------------------------------------------------------------- version history
def body_for(name, n):
    nodes = [node("t", "trigger")] + [node(f"n{i}", "note") for i in range(n)]
    return {"name": name, "description": "", "nodes": nodes, "edges": []}


def test_saving_creates_history_and_restore_works(client):
    wid = make_flow(client, [node("t", "trigger")], [])
    assert client.get(f"/api/workflows/{wid}/versions").json() == []
    client.put(f"/api/workflows/{wid}", json=body_for("v2", 1))
    client.put(f"/api/workflows/{wid}", json=body_for("v3", 2))
    versions = client.get(f"/api/workflows/{wid}/versions").json()
    assert [v["name"] for v in versions] == ["v2", "t"] and [v["nodes"] for v in versions] == [2, 1]
    assert versions[0]["saved_by"] == "owner"
    old = client.get(f"/api/workflows/{wid}/versions/{versions[-1]['id']}").json()
    assert old["name"] == "t" and len(old["nodes"]) == 1
    assert client.post(f"/api/workflows/{wid}/versions/{versions[-1]['id']}/restore").status_code == 200
    now_doc = client.get(f"/api/workflows/{wid}").json()
    assert now_doc["name"] == "t" and len(now_doc["nodes"]) == 1
    assert client.get(f"/api/workflows/{wid}/versions").json()[0]["name"] == "v3"  # restore is undoable


def test_unchanged_saves_do_not_add_versions(client):
    wid = make_flow(client, [node("t", "trigger")], [])
    same = {"name": "t", "description": "", "nodes": [node("t", "trigger")], "edges": []}
    client.put(f"/api/workflows/{wid}", json=same)
    client.put(f"/api/workflows/{wid}", json=same)
    assert client.get(f"/api/workflows/{wid}/versions").json() == []


def test_history_is_capped_and_deleted_with_the_workflow(client):
    wid = make_flow(client, [node("t", "trigger")], [])
    for i in range(main.MAX_VERSIONS + 5):
        client.put(f"/api/workflows/{wid}", json=body_for(f"v{i}", 0))
    assert len(client.get(f"/api/workflows/{wid}/versions").json()) == main.MAX_VERSIONS
    client.delete(f"/api/workflows/{wid}")
    with main.conn() as db:
        assert db.execute("SELECT COUNT(*) c FROM workflow_versions WHERE workflow_id=?", (wid,)).fetchone()["c"] == 0


def test_version_lookups_are_scoped_to_their_workflow(client):
    a = make_flow(client, [node("t", "trigger")], [], name="a")
    b = make_flow(client, [node("t", "trigger")], [], name="b")
    client.put(f"/api/workflows/{a}", json=body_for("a2", 1))
    vid = client.get(f"/api/workflows/{a}/versions").json()[0]["id"]
    assert client.get(f"/api/workflows/{b}/versions/{vid}").status_code == 404
    assert client.post(f"/api/workflows/{b}/versions/{vid}/restore").status_code == 404
    assert client.get("/api/workflows/missing/versions").status_code == 404


def test_viewer_can_view_history_but_not_restore(client):
    wid = make_flow(client, [node("t", "trigger")], [])
    client.put(f"/api/workflows/{wid}", json=body_for("v2", 1))
    add_user(client, "vivi", "viewer")
    v, _ = login("vivi")
    vid = v.get(f"/api/workflows/{wid}/versions").json()[0]["id"]
    assert v.get(f"/api/workflows/{wid}/versions/{vid}").status_code == 200
    assert v.post(f"/api/workflows/{wid}/versions/{vid}/restore").status_code == 403
