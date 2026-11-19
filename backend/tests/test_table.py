import json

import pytest
from fastapi.testclient import TestClient

from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    from app import main
    def wipe():
        with main.conn() as db: db.execute("DELETE FROM data_rows")
    wipe()
    yield
    wipe()


def tab(client, input_json="{}", **fields):
    nodes = [node("t", "trigger"), node("i", "input", json=input_json), node("d", "table", **fields), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "d"), edge("d", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


def ok(res): assert res["status"] == "success", res.get("error"); return res["output"]


def failed(res): assert res["status"] == "failed"; return res["error"]


def test_insert_find_get_count(client):
    a = ok(tab(client, '{"email": "a@b.co"}', table="leads", operation="insert", row='{"email": "{{email}}", "plan": "free", "n": 1}'))
    assert a["email"] == "a@b.co" and isinstance(a["_id"], int)
    b = ok(tab(client, table="leads", operation="insert", row='{"email": "c@d.io", "plan": "pro"}'))
    assert ok(tab(client, table="leads", operation="count")) == {"count": 2}
    assert ok(tab(client, table="leads", operation="find", filter='{"plan": "pro"}')) == [b]
    assert [r["email"] for r in ok(tab(client, table="leads", operation="find", order="newest"))] == ["c@d.io", "a@b.co"]
    assert len(ok(tab(client, table="leads", operation="find", limit="1"))) == 1
    assert ok(tab(client, table="leads", operation="get", row_id=str(a["_id"]))) == a
    assert ok(tab(client, table="leads", operation="get", row_id="99999")) is None
    assert failed(tab(client, table="leads", operation="get", row_id="x"))


def test_tables_are_separate_and_shared_between_runs(client):
    ok(tab(client, table="one", operation="insert", row='{"a": 1}'))
    assert ok(tab(client, table="two", operation="count")) == {"count": 0}
    assert ok(tab(client, table="one", operation="count")) == {"count": 1}


def test_update_and_delete_need_filters(client):
    ok(tab(client, table="t", operation="insert", row='{"k": "x", "v": 1}'))
    ok(tab(client, table="t", operation="insert", row='{"k": "y", "v": 1}'))
    assert ok(tab(client, table="t", operation="update", filter='{"k": "x"}', values='{"v": 2}')) == {"updated": 1}
    assert [r["v"] for r in ok(tab(client, table="t", operation="find", filter='{"k": "x"}'))] == [2]
    assert [r["v"] for r in ok(tab(client, table="t", operation="find", filter='{"k": "y"}'))] == [1]
    assert failed(tab(client, table="t", operation="update", values='{"v": 9}'))
    assert failed(tab(client, table="t", operation="delete"))
    assert ok(tab(client, table="t", operation="count")) == {"count": 2}
    assert ok(tab(client, table="t", operation="delete", filter='{"k": "y"}')) == {"deleted": 1}
    assert ok(tab(client, table="t", operation="clear")) == {"deleted": 1}


def test_filter_matches_nested_and_numbers(client):
    ok(tab(client, table="n", operation="insert", row='{"user": {"id": 7}, "n": 3}'))
    assert len(ok(tab(client, table="n", operation="find", filter='{"user.id": 7}'))) == 1
    assert len(ok(tab(client, table="n", operation="find", filter='{"n": "3"}'))) == 1
    assert ok(tab(client, table="n", operation="find", filter='{"n": 4}')) == []


def test_insert_every_item_and_dedupe_use_case(client):
    assert ok(tab(client, '[{"id": 1}, {"id": 2}, {"id": 3}]', table="b", operation="insert_all")) == {"inserted": 3}
    assert ok(tab(client, table="b", operation="count")) == {"count": 3}
    assert failed(tab(client, '{"id": 1}', table="b", operation="insert_all"))
    assert failed(tab(client, '[1, 2]', table="b", operation="insert_all"))


def test_validation(client):
    for bad in ("", "1abc", "has space", "x" * 41, "a;drop"):
        assert failed(tab(client, table=bad, operation="count")), bad
    assert failed(tab(client, table="t", operation="insert", row="[1]"))
    assert failed(tab(client, table="t", operation="insert", row="{}"))
    assert failed(tab(client, table="t", operation="find", filter="[1]"))
    assert failed(tab(client, table="t", operation="explode"))
    assert failed(tab(client, table="t", operation="insert", row=json.dumps({"big": "x" * 70000})))


def test_limits(client, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "MAX_TABLE_ROWS", 2)
    ok(tab(client, table="cap", operation="insert", row='{"a": 1}'))
    ok(tab(client, table="cap", operation="insert", row='{"a": 2}'))
    assert "at most 2 rows" in failed(tab(client, table="cap", operation="insert", row='{"a": 3}'))
    monkeypatch.setattr(main, "MAX_TABLES", 1)
    assert "At most 1 tables" in failed(tab(client, table="other", operation="insert", row='{"a": 1}'))


def test_row_values_are_data_not_templates(client):
    ok(tab(client, '{"email": "x\\"y@z.io"}', table="q", operation="insert", row='{"email": "{{email}}"}'))
    assert ok(tab(client, table="q", operation="find"))[0]["email"] == 'x"y@z.io'


def test_tables_api_lists_reads_and_deletes(client):
    assert client.get("/api/tables").json() == []
    a = ok(tab(client, table="people", operation="insert", row='{"name": "Ada", "tags": ["x"]}'))
    ok(tab(client, table="people", operation="insert", row='{"name": "Bob"}'))
    listing = client.get("/api/tables").json()
    assert [(t["name"], t["rows"]) for t in listing] == [("people", 2)]
    data = client.get("/api/tables/people").json()
    assert data["total"] == 2 and [r["name"] for r in data["rows"]] == ["Bob", "Ada"] and data["rows"][1]["tags"] == ["x"] and "added" in data["rows"][0]
    assert len(client.get("/api/tables/people?limit=1").json()["rows"]) == 1
    assert client.delete(f"/api/tables/people/rows/{a['_id']}").json() == {"deleted": 1}
    assert client.delete(f"/api/tables/people/rows/{a['_id']}").status_code == 404
    assert client.delete("/api/tables/people").json() == {"deleted": 1}
    assert client.get("/api/tables/people").status_code == 404
    assert client.get("/api/tables/bad name").status_code == 404 and client.delete("/api/tables/1bad").status_code == 404


def test_tables_api_needs_sign_in(client):
    from fastapi.testclient import TestClient
    from app import main
    with TestClient(main.app) as anon:
        assert anon.get("/api/tables").status_code == 401
        assert anon.delete("/api/tables/x").status_code == 401


def test_workflow_list_carries_search_text(client):
    wid = make_flow(client, [node("t", "trigger"), node("d", "table", table="Quarterly_Leads", operation="count"), node("o", "output")], [edge("t", "d"), edge("d", "o")], name="Search me")
    row = next(w for w in client.get("/api/workflows").json() if w["id"] == wid)
    assert "quarterly_leads" in row["search"] and "table" in row["search"]


# ---------------------------------------------------------------- projects
def _project_world(client):
    from fastapi.testclient import TestClient
    from app.main import app
    a = client.post("/api/projects", json={"name": "Alpha"}).json()["id"]
    b = client.post("/api/projects", json={"name": "Beta"}).json()["id"]
    ids = {}
    for name in ("ann", "bob"):
        res = client.post("/api/users", json={"username": name, "password": "long-enough-pw", "role": "editor"})
        assert res.status_code == 201
        ids[name] = res.json()["id"]
    client.put(f"/api/projects/{a}", json={"user_ids": [ids["ann"]]})
    client.put(f"/api/projects/{b}", json={"user_ids": [ids["bob"]]})
    people = {}
    for name in ("ann", "bob"):
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"username": name, "password": "long-enough-pw"}).status_code == 200
        people[name] = c
    return a, b, people


def _table_flow(client, project, row):
    nodes = [node("t", "trigger"), node("d", "table", table="shared", operation="insert", row=row), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "d"), edge("d", "o")])
    if project:
        from app import main
        with main.conn() as db: db.execute("UPDATE workflows SET project_id=? WHERE id=?", (project, wid))
    return wid


@pytest.fixture()
def project_cleanup(client):
    yield
    from app import main
    with main.conn() as db:
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM projects")
        db.execute("UPDATE workflows SET project_id=NULL")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def test_each_project_has_its_own_tables_with_the_same_name(client, project_cleanup):
    a, b, people = _project_world(client)
    for project, who in ((a, "alpha"), (b, "beta"), (None, "plain")):
        wid = _table_flow(client, project, json.dumps({"who": who}))
        assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success"
    # a workflow only finds the rows of its own project
    def finder(project):
        nodes = [node("t", "trigger"), node("d", "table", table="shared", operation="find"), node("o", "output")]
        wid = make_flow(client, nodes, [edge("t", "d"), edge("d", "o")])
        if project:
            from app import main
            with main.conn() as db: db.execute("UPDATE workflows SET project_id=? WHERE id=?", (project, wid))
        return [r["who"] for r in client.post(f"/api/workflows/{wid}/run").json()["output"]]
    assert finder(a) == ["alpha"] and finder(b) == ["beta"] and finder(None) == ["plain"]


def test_people_only_see_and_change_tables_of_their_projects(client, project_cleanup):
    a, b, people = _project_world(client)
    for project, who in ((a, "alpha"), (b, "beta"), (None, "plain")):
        client.post(f"/api/workflows/{_table_flow(client, project, json.dumps({'who': who}))}/run")
    owner_view = {(t["project_id"], t["name"]) for t in client.get("/api/tables").json()}
    assert owner_view == {(a, "shared"), (b, "shared"), (None, "shared")}
    ann = {(t["project_id"], t["project_name"]) for t in people["ann"].get("/api/tables").json()}
    assert ann == {(a, "Alpha"), (None, None)}
    assert people["ann"].get("/api/tables/shared", params={"project_id": a}).json()["rows"][0]["who"] == "alpha"
    assert people["ann"].get("/api/tables/shared").json()["rows"][0]["who"] == "plain"
    # Beta's table looks like it does not exist to Ann, for reading and for changing
    assert people["ann"].get("/api/tables/shared", params={"project_id": b}).status_code == 404
    beta_row = client.get("/api/tables/shared", params={"project_id": b}).json()["rows"][0]["_id"]
    assert people["ann"].delete(f"/api/tables/shared/rows/{beta_row}", params={"project_id": b}).status_code == 404
    assert people["ann"].delete("/api/tables/shared", params={"project_id": b}).status_code == 404
    assert client.get("/api/tables/shared", params={"project_id": b}).json()["total"] == 1
    # her own table can be changed
    own_row = people["ann"].get("/api/tables/shared", params={"project_id": a}).json()["rows"][0]["_id"]
    assert people["ann"].delete(f"/api/tables/shared/rows/{own_row}", params={"project_id": a}).status_code == 200
    # the unassigned table and the other project's table are separate rows
    assert client.get("/api/tables/shared").json()["total"] == 1
    assert client.delete("/api/tables/shared", params={"project_id": b}).json() == {"deleted": 1}
