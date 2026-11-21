import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _env(clean_state):
    with main.conn() as db: db.execute("DELETE FROM executions")
    yield
    with main.conn() as db:
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM projects")
        db.execute("UPDATE workflows SET project_id=NULL")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def one_run(client, name="Noted flow", project=None):
    res = client.post("/api/workflows", params={"project_id": project} if project else None, json={"name": name, "description": "", "nodes": [node("t", "trigger"), node("o", "output")], "edges": [edge("t", "o")]})
    wid = res.json()["id"]
    return client.post(f"/api/workflows/{wid}/run").json()["id"]


def note(client, rid, text):
    return client.put(f"/api/executions/{rid}/note", json={"note": text})


def listed(client, rid):
    return next(r for r in client.get("/api/executions").json() if r["id"] == rid)


def test_a_note_is_saved_and_shown_in_the_list(client):
    rid = one_run(client)
    assert listed(client, rid)["note"] is None
    assert note(client, rid, "  customer confirmed fixed  ").json() == {"id": rid, "note": "customer confirmed fixed"}
    assert listed(client, rid)["note"] == "customer confirmed fixed"


def test_an_empty_note_clears_it_and_a_new_one_replaces_it(client):
    rid = one_run(client)
    note(client, rid, "first")
    note(client, rid, "second")
    assert listed(client, rid)["note"] == "second"
    assert note(client, rid, "   ").json()["note"] is None and listed(client, rid)["note"] is None


def test_notes_are_limited_and_runs_must_exist(client):
    rid = one_run(client)
    assert note(client, rid, "x" * 501).status_code == 422
    assert note(client, rid, "x" * 500).status_code == 200
    assert note(client, "no-such-run", "hi").status_code == 404


def test_notes_can_be_searched(client):
    rid = one_run(client)
    other = one_run(client, "Other flow")
    note(client, rid, "escalated to Priya on Friday")
    res = client.get("/api/executions/search", params={"q": "priya"}).json()
    assert [r["id"] for r in res["runs"]] == [rid] and res["runs"][0]["note"] == "escalated to Priya on Friday"
    assert other not in [r["id"] for r in res["runs"]]


def test_viewers_cannot_write_notes_but_can_read_them(client):
    rid = one_run(client)
    note(client, rid, "visible to all")
    client.post("/api/users", json={"username": "noteviewer", "password": "long-enough-pw", "role": "viewer"})
    viewer = TestClient(app)
    assert viewer.post("/api/auth/login", json={"username": "noteviewer", "password": "long-enough-pw"}).status_code == 200
    assert note(viewer, rid, "tampered").status_code == 403
    assert listed(viewer, rid)["note"] == "visible to all"


def test_people_cannot_note_runs_of_workflows_they_cannot_see(client):
    a = client.post("/api/projects", json={"name": "NoteAlpha"}).json()["id"]
    b = client.post("/api/projects", json={"name": "NoteBeta"}).json()["id"]
    uid = client.post("/api/users", json={"username": "noteeditor", "password": "long-enough-pw", "role": "editor"}).json()["id"]
    client.put(f"/api/projects/{a}", json={"user_ids": [uid]})
    mine, theirs = one_run(client, "Mine", a), one_run(client, "Theirs", b)
    me = TestClient(app)
    assert me.post("/api/auth/login", json={"username": "noteeditor", "password": "long-enough-pw"}).status_code == 200
    assert note(me, mine, "ok").status_code == 200
    assert note(me, theirs, "no").status_code == 404
    assert listed(client, theirs)["note"] is None


def test_a_note_needs_a_login(anon):
    assert anon.put("/api/executions/x/note", json={"note": "hi"}).status_code == 401
