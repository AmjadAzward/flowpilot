import json
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db: db.execute("DELETE FROM variables")


def var(client, name, value):
    res = client.put(f"/api/variables/{name}", json={"value": value})
    assert res.status_code == 200, res.text


def run_template(client, template, data='{"price": 10, "name": "Ada"}'):
    nodes = [node("t", "trigger"), node("i", "input", json=data), node("m", "template", template=template), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "m"), edge("m", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


# ---------------------------------------------------------------- storage
def test_create_list_update_delete(client):
    var(client, "SITE_URL", "https://example.com")
    var(client, "TAX_RATE", "1.2")
    rows = {r["name"]: r for r in client.get("/api/variables").json()}
    assert rows["SITE_URL"]["value"] == "https://example.com" and rows["TAX_RATE"]["updated_at"]
    var(client, "TAX_RATE", "1.25")
    assert {r["name"]: r["value"] for r in client.get("/api/variables").json()}["TAX_RATE"] == "1.25"
    assert client.delete("/api/variables/TAX_RATE").status_code == 204
    assert "TAX_RATE" not in {r["name"] for r in client.get("/api/variables").json()}
    assert client.delete("/api/variables/never-existed").status_code == 204


def test_names_and_values_are_validated(client):
    for bad in ("1abc", "has space", "a" * 41, "dots.not.ok"):
        assert client.put(f"/api/variables/{bad}", json={"value": "x"}).status_code in (400, 404, 422), bad
    assert client.put("/api/variables/OK_NAME", json={"value": "x" * 2001}).status_code == 422
    assert client.put("/api/variables/OK_NAME", json={"value": ""}).status_code == 200  # empty is allowed


def test_there_is_a_limit_on_how_many_variables_exist(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_VARIABLES", 3)
    for i in range(3): var(client, f"V{i}", "x")
    assert client.put("/api/variables/V9", json={"value": "x"}).status_code == 400
    assert client.put("/api/variables/V0", json={"value": "changed"}).status_code == 200  # existing ones can still change


def test_only_admins_can_change_variables_but_everyone_can_read(client):
    var(client, "SHARED", "hello")
    roles = {}
    for role in ("viewer", "editor", "admin"):
        client.post("/api/users", json={"username": f"var-{role}", "password": "long-enough-pw", "role": role})
        c = TestClient(main.app)
        assert c.post("/api/auth/login", json={"username": f"var-{role}", "password": "long-enough-pw"}).status_code == 200
        roles[role] = c
    assert roles["viewer"].get("/api/variables").status_code == 200
    for role in ("viewer", "editor"):
        assert roles[role].put("/api/variables/SHARED", json={"value": "no"}).status_code == 403
        assert roles[role].delete("/api/variables/SHARED").status_code == 403
    assert roles["admin"].put("/api/variables/SHARED", json={"value": "yes"}).status_code == 200
    users = client.get("/api/users").json()
    for role in roles: client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'var-' + role)}")


# ---------------------------------------------------------------- using them
def test_variables_work_in_templates(client):
    var(client, "SITE", "https://example.com")
    res = run_template(client, "Visit {{vars.SITE}} now, {{name}}!")
    assert res["status"] == "success" and res["output"] == "Visit https://example.com now, Ada!"


def test_variables_work_inside_expressions(client):
    var(client, "RATE", "1.5")
    var(client, "LABEL", "total")
    res = run_template(client, "{{ vars.LABEL }}: {{ float(vars.RATE) * price }}")
    assert res["output"] == "total: 15.0"


def test_variables_are_escaped_in_json_contexts(client):
    var(client, "QUOTED", 'say "hi"')
    nodes = [node("t", "trigger"), node("i", "input", json="{}"), node("s", "set", json='{"msg": "{{vars.QUOTED}}"}'), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "s"), edge("s", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["output"] == {"msg": 'say "hi"'}


def test_variables_work_in_http_urls(client, fake_http):
    var(client, "API_HOST", "api.example.com")
    nodes = [node("t", "trigger"), node("h", "http", url="https://{{vars.API_HOST}}/v1/items", method="GET"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "h"), edge("h", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success"
    assert fake_http.calls[-1]["url"] == "https://api.example.com/v1/items"


def test_an_undefined_variable_fails_the_run_clearly(client):
    res = run_template(client, "{{vars.MISSING_ONE}}")
    assert res["status"] == "failed" and "MISSING_ONE" in res["error"] and "not defined" in res["error"]


def test_changes_apply_to_the_next_run(client):
    var(client, "GREETING", "Hello")
    wid = make_flow(client, [node("t", "trigger"), node("m", "template", template="{{vars.GREETING}}"), node("o", "output")], [edge("t", "m"), edge("m", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["output"] == "Hello"
    var(client, "GREETING", "Goodbye")
    assert client.post(f"/api/workflows/{wid}/run").json()["output"] == "Goodbye"


def test_incoming_data_with_its_own_vars_field_keeps_working(client):
    var(client, "X", "from-variable")
    res = run_template(client, "{{vars.x}}", data='{"vars": {"x": "from-data"}}')
    assert res["output"] == "from-data"  # existing workflows that read a field called vars are not broken


def test_variables_reach_loops_and_sub_workflows(client):
    var(client, "PREFIX", "item")
    inner = make_flow(client, [node("t", "trigger"), node("m", "template", template="{{vars.PREFIX}}-{{}}"), node("o", "output")], [edge("t", "m"), edge("m", "o")], name="inner")
    nodes = [node("t", "trigger"), node("i", "input", json="[1, 2]"), node("l", "loop", path=""), node("s", "subflow", workflow_id=inner), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "l"), edge("l", "s", "each"), edge("l", "o", "done")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success" and res["output"] == ["item-1", "item-2"]


def test_variables_are_not_treated_as_secrets(client):
    var(client, "VISIBLE", "plain-visible-value")
    res = run_template(client, "{{vars.VISIBLE}}")
    assert res["output"] == "plain-visible-value"  # not redacted: use credentials for secrets


# ---------------------------------------------------------------- Google Calendar and Drive
def connect(name="GOOGLE"):
    main.store_credential(name, json.dumps({"oauth2": 1, "provider": "google", "client_id": "cid", "client_secret": "csecret", "token_url": "https://oauth2.googleapis.com/token",
                                            "scopes": "x", "access_token": "at-valid-token-123", "refresh_token": "rt", "expires_at": time.time() + 3600}))


def app_run(client, service, operation, fields, cred="GOOGLE"):
    nodes = [node("t", "trigger"), node("a", "app", service=service, operation=operation, credential=cred, fields=json.dumps(fields)), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "a"), edge("a", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


def test_calendar_lists_events(client, fake_http):
    connect()
    fake_http.reply["response"] = FakeResponse(200, {"items": [{"id": "e1", "summary": "Standup", "start": {"dateTime": "2026-02-01T09:00:00Z"}, "end": {"date": "2026-02-02"}, "location": "Room 1", "htmlLink": "https://cal/e1", "extra": 1}]})
    res = app_run(client, "gcalendar", "list_events", {"limit": 5, "time_min": "2026-02-01"})
    assert res["status"] == "success" and res["output"] == [{"id": "e1", "summary": "Standup", "start": "2026-02-01T09:00:00Z", "end": "2026-02-02", "location": "Room 1", "link": "https://cal/e1"}]
    call = fake_http.calls[-1]
    assert call["url"] == "https://www.googleapis.com/calendar/v3/calendars/primary/events" and call["headers"]["Authorization"] == "Bearer at-valid-token-123"
    assert call["params"]["maxResults"] == 5 and call["params"]["timeMin"] == "2026-02-01T00:00:00+00:00" and call["params"]["singleEvents"] == "true"


def test_calendar_creates_timed_and_all_day_events(client, fake_http):
    connect()
    fake_http.reply["response"] = FakeResponse(200, {"id": "n1", "htmlLink": "https://cal/n1", "summary": "Launch"})
    res = app_run(client, "gcalendar", "create_event", {"summary": "Launch", "start": "2026-03-01T10:00:00Z", "end": "2026-03-01T11:00:00Z", "location": "HQ", "calendar_id": "team@group.calendar.google.com"})
    assert res["output"] == {"id": "n1", "link": "https://cal/n1", "summary": "Launch"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://www.googleapis.com/calendar/v3/calendars/team%40group.calendar.google.com/events"
    assert call["json"]["start"] == {"dateTime": "2026-03-01T10:00:00+00:00"} and call["json"]["location"] == "HQ"
    app_run(client, "gcalendar", "create_event", {"summary": "Holiday", "start": "2026-04-01", "end": "2026-04-02"})
    assert fake_http.calls[-1]["json"]["start"] == {"date": "2026-04-01"} and fake_http.calls[-1]["json"]["end"] == {"date": "2026-04-02"}


def test_calendar_validates(client, fake_http):
    connect()
    for bad in ({"summary": "x", "start": "not a date", "end": "2026-01-01"}, {"summary": "", "start": "2026-01-01", "end": "2026-01-02"}, {"summary": "x", "start": "2026-01-01", "end": "2026-01-02", "calendar_id": "../x"}):
        assert app_run(client, "gcalendar", "create_event", bad)["status"] == "failed", bad
    assert not fake_http.calls


def test_drive_lists_and_creates_folders(client, fake_http):
    connect()
    fake_http.reply["response"] = FakeResponse(200, {"files": [{"id": "f1", "name": "Plan", "mimeType": "text/plain", "modifiedTime": "2026-01-01T00:00:00Z", "webViewLink": "https://drive/f1", "size": "12", "owners": ["x"]}]})
    res = app_run(client, "gdrive", "list_files", {"name_contains": "O'Brien \\ plan", "folder_id": "abcdefghij1234", "limit": 7})
    assert res["output"] == [{"id": "f1", "name": "Plan", "type": "text/plain", "modified": "2026-01-01T00:00:00Z", "url": "https://drive/f1", "size": "12"}]
    q = fake_http.calls[-1]["params"]["q"]
    assert q == "trashed = false and name contains 'O\\'Brien \\\\ plan' and 'abcdefghij1234' in parents" and fake_http.calls[-1]["params"]["pageSize"] == 7
    fake_http.reply["response"] = FakeResponse(200, {"id": "d1", "name": "Reports", "webViewLink": "https://drive/d1"})
    made = app_run(client, "gdrive", "create_folder", {"name": "Reports", "folder_id": "abcdefghij1234"})
    assert made["output"] == {"id": "d1", "name": "Reports", "url": "https://drive/d1"}
    assert fake_http.calls[-1]["json"] == {"name": "Reports", "mimeType": "application/vnd.google-apps.folder", "parents": ["abcdefghij1234"]}
    assert app_run(client, "gdrive", "list_files", {"folder_id": "bad id"})["status"] == "failed"


def test_google_services_need_an_oauth_connection(client, fake_http):
    main.store_credential("PLAIN", "just-a-plain-secret-value")
    res = app_run(client, "gcalendar", "list_events", {}, cred="PLAIN")
    assert res["status"] == "failed" and "not an OAuth connection" in res["error"] and not fake_http.calls
    assert app_run(client, "gdrive", "list_files", {}, cred="")["status"] == "failed"
