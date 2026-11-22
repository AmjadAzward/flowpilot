import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app, normalize_tags
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _env(clean_state):
    with main.conn() as db: db.execute("DELETE FROM executions")
    yield
    with main.conn() as db:
        db.execute("DELETE FROM executions")
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM projects")
        db.execute("UPDATE workflows SET project_id=NULL")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def flow(client, name="Tag flow"):
    return make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name=name)


def add_run(wid):
    rid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with main.conn() as db:
        db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                   (rid, wid, "success", now, now, "null", "[]", None, "manual"))
    return rid


def put(client, rid, tags):
    return client.put(f"/api/executions/{rid}/tags", json={"tags": tags})


def listed(client, rid):
    return next(r for r in client.get("/api/executions").json() if r["id"] == rid)


# ------------------------------------------------------------ cleaning up what people type
def test_tags_are_lower_cased_and_spaces_become_dashes():
    assert normalize_tags(["Needs Review", "  BILLING "]) == ["needs-review", "billing"]


def test_duplicates_and_empty_tags_drop_out():
    assert normalize_tags(["a", "A", "", "  ", "a"]) == ["a"]


def test_odd_characters_are_refused():
    with pytest.raises(Exception) as e: normalize_tags(["no/slashes"])
    assert "may only use" in str(e.value.detail)


def test_long_tags_and_too_many_tags_are_refused():
    with pytest.raises(Exception): normalize_tags(["x" * 25])
    with pytest.raises(Exception): normalize_tags([f"t{i}" for i in range(9)])
    assert len(normalize_tags([f"t{i}" for i in range(8)])) == 8


# ------------------------------------------------------------ the endpoints
def test_tags_are_saved_and_shown_on_the_run(client):
    rid = add_run(flow(client))
    res = put(client, rid, ["Billing", "needs review"])
    assert res.status_code == 200 and res.json()["tags"] == ["billing", "needs-review"]
    assert listed(client, rid)["tags"] == ["billing", "needs-review"]


def test_a_run_without_tags_has_an_empty_list(client):
    assert listed(client, add_run(flow(client)))["tags"] == []


def test_an_empty_list_clears_the_tags(client):
    rid = add_run(flow(client))
    put(client, rid, ["a"])
    assert put(client, rid, []).json()["tags"] == [] and listed(client, rid)["tags"] == []


def test_bad_tags_and_unknown_runs_are_rejected(client):
    rid = add_run(flow(client))
    assert put(client, rid, ["bad/tag"]).status_code == 400
    assert put(client, "nope", ["a"]).status_code == 404
    assert listed(client, rid)["tags"] == []


def test_counts_list_each_tag_once_most_used_first(client):
    wid = flow(client)
    a, b, c = add_run(wid), add_run(wid), add_run(wid)
    put(client, a, ["billing", "urgent"]); put(client, b, ["billing"]); put(client, c, ["zzz"])
    assert client.get("/api/executions/tags").json()["tags"] == [{"tag": "billing", "runs": 2}, {"tag": "urgent", "runs": 1}, {"tag": "zzz", "runs": 1}]


def test_tags_can_be_searched(client):
    rid = add_run(flow(client))
    put(client, rid, ["needs-review"])
    hits = client.get("/api/executions/search", params={"q": "needs-review"}).json()
    assert [r["id"] for r in hits["runs"]] == [rid]


def test_login_is_needed(client, anon):
    rid = add_run(flow(client))
    assert anon.put(f"/api/executions/{rid}/tags", json={"tags": ["a"]}).status_code == 401
    assert anon.get("/api/executions/tags").status_code == 401


def test_viewers_cannot_tag_and_people_cannot_tag_or_count_hidden_runs(client):
    pa = client.post("/api/projects", json={"name": "TagAlpha"}).json()["id"]
    pb = client.post("/api/projects", json={"name": "TagBeta"}).json()["id"]
    uid = client.post("/api/users", json={"username": "taguser", "password": "long-enough-pw", "role": "editor"}).json()["id"]
    vid = client.post("/api/users", json={"username": "tagviewer", "password": "long-enough-pw", "role": "viewer"}).json()["id"]
    client.put(f"/api/projects/{pa}", json={"user_ids": [uid, vid]})
    mine = client.post("/api/workflows", params={"project_id": pa}, json={"name": "Mine", "description": "", "nodes": [], "edges": []}).json()["id"]
    theirs = client.post("/api/workflows", params={"project_id": pb}, json={"name": "Theirs", "description": "", "nodes": [], "edges": []}).json()["id"]
    my_run, their_run = add_run(mine), add_run(theirs)
    put(client, their_run, ["secret-project"])
    ed = TestClient(app)
    assert ed.post("/api/auth/login", json={"username": "taguser", "password": "long-enough-pw"}).status_code == 200
    assert ed.put(f"/api/executions/{my_run}/tags", json={"tags": ["ok"]}).status_code == 200
    assert ed.put(f"/api/executions/{their_run}/tags", json={"tags": ["x"]}).status_code in (403, 404)
    assert [t["tag"] for t in ed.get("/api/executions/tags").json()["tags"]] == ["ok"]
    vw = TestClient(app)
    assert vw.post("/api/auth/login", json={"username": "tagviewer", "password": "long-enough-pw"}).status_code == 200
    assert vw.put(f"/api/executions/{my_run}/tags", json={"tags": ["nope"]}).status_code == 403
