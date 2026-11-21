import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _env(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM executions")
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM projects")
        db.execute("UPDATE workflows SET project_id=NULL")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def greeter(client, name="Order flow", project=None):
    nodes = [node("t", "trigger"), node("g", "set", json='{"msg": "Order {{order}} for {{who}}"}'), node("o", "output")]
    res = client.post("/api/workflows", params={"project_id": project} if project else None, json={"name": name, "description": "", "nodes": nodes, "edges": [edge("t", "g"), edge("g", "o")]})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def run(client, wid, **inp):
    return client.post(f"/api/workflows/{wid}/run", json={"input": inp}).json()


def search(client, q, **params):
    return client.get("/api/executions/search", params={"q": q, **params})


def test_finds_the_run_that_handled_a_value(client):
    with main.conn() as db: db.execute("DELETE FROM executions")
    wid = greeter(client)
    target = run(client, wid, order="A-1042", who="Ada")
    run(client, wid, order="B-7", who="Bob")
    res = search(client, "a-1042").json()  # case does not matter
    assert [r["id"] for r in res["runs"]] == [target["id"]] and res["matches"] == 1
    assert "A-1042" in res["runs"][0]["snippet"] and res["runs"][0]["steps"]  # the full run comes back, ready to show


def test_no_match_returns_nothing(client):
    wid = greeter(client)
    run(client, wid, order="A-1", who="Ada")
    assert search(client, "zzz-not-there").json()["runs"] == []


def test_can_be_limited_to_one_workflow(client):
    one, two = greeter(client, "One"), greeter(client, "Two")
    run(client, one, order="SHARED-9", who="x")
    run(client, two, order="SHARED-9", who="y")
    assert search(client, "SHARED-9").json()["matches"] == 2
    res = search(client, "SHARED-9", workflow_id=two).json()
    assert res["matches"] == 1 and res["runs"][0]["workflow_id"] == two


def test_non_ascii_text_is_found(client):
    wid = greeter(client)
    run(client, wid, order="1", who="Zoë Müller")
    assert search(client, "müller").json()["matches"] == 1


def test_error_text_is_searchable(client):
    wid = make_flow(client, [node("t", "trigger"), node("f", "fail", message="warehouse offline"), node("o", "output")], [edge("t", "f"), edge("f", "o")], name="Breaks")
    client.post(f"/api/workflows/{wid}/run")
    assert search(client, "warehouse offline").json()["matches"] >= 1


def test_search_text_must_be_sensible(client):
    assert search(client, "a").status_code == 400
    assert search(client, "   ").status_code == 400
    assert search(client, "x" * 201).status_code == 400
    assert client.get("/api/executions/search").status_code == 422


def test_results_are_capped_but_the_total_is_reported(client, monkeypatch):
    wid = greeter(client)
    for i in range(4): run(client, wid, order=f"CAP-{i}", who="x")
    monkeypatch.setattr(main, "SEARCH_RESULTS", 2)
    res = search(client, "CAP-").json()
    assert len(res["runs"]) == 2 and res["matches"] == 4


def test_a_credential_value_never_turns_up_in_a_search(client, fake_http):
    client.put("/api/credentials/SEARCHKEY", json={"value": "sk-search-secret-77"})
    wid = make_flow(client, [node("t", "trigger"), node("h", "http", url="https://api.example.com/x", method="GET", headers='{"Authorization": "Bearer {{cred.SEARCHKEY}}"}'), node("o", "output")], [edge("t", "h"), edge("h", "o")], name="Keyed")
    client.post(f"/api/workflows/{wid}/run")
    assert search(client, "sk-search-secret-77").json()["matches"] == 0


def test_search_needs_a_login(anon):
    assert anon.get("/api/executions/search", params={"q": "abc"}).status_code == 401


def test_people_only_find_runs_of_workflows_they_can_see(client):
    a = client.post("/api/projects", json={"name": "SearchAlpha"}).json()["id"]
    b = client.post("/api/projects", json={"name": "SearchBeta"}).json()["id"]
    uid = client.post("/api/users", json={"username": "searcher", "password": "long-enough-pw", "role": "editor"}).json()["id"]
    client.put(f"/api/projects/{a}", json={"user_ids": [uid]})
    mine, theirs = greeter(client, "Mine", a), greeter(client, "Theirs", b)
    run(client, mine, order="VISIBLE-1", who="x")
    run(client, theirs, order="VISIBLE-1", who="y")
    me = TestClient(app)
    assert me.post("/api/auth/login", json={"username": "searcher", "password": "long-enough-pw"}).status_code == 200
    res = search(me, "VISIBLE-1").json()
    assert res["matches"] == 1 and res["runs"][0]["workflow_id"] == mine
    assert search(me, "VISIBLE-1", workflow_id=theirs).json()["runs"] == []
    assert search(client, "VISIBLE-1").json()["matches"] == 2  # the owner sees both
