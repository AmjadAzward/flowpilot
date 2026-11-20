import json

import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_FILES_DIR", str(tmp_path))
    with main.conn() as db:
        db.execute("DELETE FROM data_rows")
        db.execute("DELETE FROM executions")
    yield
    with main.conn() as db: db.execute("DELETE FROM data_rows")


def build(client, *middle, name="Dry"):
    nodes = [node("t", "trigger"), node("i", "input", json='{"name": "Ada", "email": "ada@example.com"}'), *middle, node("o", "output")]
    chain = ["t", "i", *[n["id"] for n in middle], "o"]
    return make_flow(client, nodes, [edge(a, b) for a, b in zip(chain, chain[1:])], name=name)


def dry(client, wid):
    return client.post(f"/api/workflows/{wid}/run?dry=true").json()


def test_a_post_is_described_but_not_sent(client, fake_http):
    wid = build(client, node("h", "http", method="POST", url="https://api.example.com/hook?who={{name}}", body='{"hello": "{{name}}"}'))
    res = dry(client, wid)
    assert res["status"] == "success" and res["dry_run"] is True and fake_http.calls == []
    step = next(s for s in res["steps"] if s["node_id"] == "h")["output"]
    assert step["dry_run"] is True and step["settings"]["url"] == "https://api.example.com/hook?who=Ada" and step["settings"]["body"] == '{"hello": "Ada"}'
    assert res["not_done"] == [{"node": "h", "type": "http", "not_done": "POST request"}]
    assert res["output"] == {"name": "Ada", "email": "ada@example.com"}  # the data flows on unchanged


def test_a_get_really_runs(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"price": 5})
    res = dry(client, build(client, node("h", "http", method="GET", url="https://api.example.com/price")))
    assert res["output"] == {"price": 5} and len(fake_http.calls) == 1 and res["not_done"] == []


def test_emails_and_chat_messages_are_not_sent(client, fake_http, monkeypatch):
    sent = []
    monkeypatch.setattr(main.smtplib, "SMTP", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(main.smtplib, "SMTP_SSL", lambda *a, **k: sent.append(a))
    client.put("/api/credentials/SMTPC", json={"value": "smtp://u:p@smtp.example.com:587?from=me@example.com"})
    client.put("/api/credentials/CHAT", json={"value": "https://hooks.slack.com/services/T000/B000/XXXX"})
    wid = build(client, node("e", "email", credential="SMTPC", to="{{email}}", subject="Hi {{name}}", body="Hello"), node("n", "notify", credential="CHAT", kind="slack", message="Hi {{name}}"))
    res = dry(client, wid)
    assert res["status"] == "success" and sent == [] and fake_http.calls == []
    mail = next(s for s in res["steps"] if s["node_id"] == "e")["output"]
    assert mail["settings"]["to"] == "ada@example.com" and mail["settings"]["subject"] == "Hi Ada" and "credential" not in mail["settings"]
    assert {a["type"] for a in res["not_done"]} == {"email", "notify"}


def test_credentials_never_show_in_a_dry_run(client, fake_http):
    client.put("/api/credentials/APIKEY", json={"value": "super-secret-token-99"})
    wid = build(client, node("h", "http", method="POST", url="https://api.example.com/x", headers='{"Authorization": "Bearer {{cred.APIKEY}}"}', body="{{cred.APIKEY}}"))
    res = dry(client, wid)
    assert "super-secret-token-99" not in json.dumps(res) and "super-secret-token-99" not in json.dumps(client.get("/api/executions").json())


def test_files_tables_and_sql_changes_are_skipped_but_reads_run(client, tmp_path):
    wid = build(client, node("f", "file", operation="write", path="out.txt", content="x"), node("a", "table", operation="insert", table="dryrun_people", data='{"n": 1}'),
                node("c", "table", operation="count", table="dryrun_people"))
    res = dry(client, wid)
    assert res["status"] == "success" and not (tmp_path / "out.txt").exists()
    assert next(s for s in res["steps"] if s["node_id"] == "c")["output"] == {"count": 0}  # the insert did not happen
    assert {a["not_done"] for a in res["not_done"]} == {"file write", "table insert"}
    (tmp_path / "in.txt").write_text("hello")
    real = dry(client, build(client, node("r", "file", operation="read", path="in.txt")))
    assert real["output"]["text"] == "hello" and real["not_done"] == []


def test_waits_do_not_pause_or_leave_a_waiting_run(client):
    wid = build(client, node("w", "wait", seconds="3600", unit="seconds"), node("d", "delay", seconds="30"), node("c", "callback", seconds="1", unit="days"))
    with main.conn() as db: before = db.execute("SELECT COUNT(*) c FROM waits WHERE status='pending'").fetchone()["c"]  # other tests may leave waits behind
    res = dry(client, wid)
    assert res["status"] == "success" and {a["type"] for a in res["not_done"]} == {"wait", "delay", "callback"}
    with main.conn() as db: assert db.execute("SELECT COUNT(*) c FROM waits WHERE status='pending'").fetchone()["c"] == before
    with main.conn() as db: assert db.execute("SELECT COUNT(*) c FROM waits WHERE workflow_id=?", (wid,)).fetchone()["c"] == 0


def test_a_failing_dry_run_does_not_call_the_error_workflow(client):
    handler = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="Handler")
    wid = make_flow(client, [node("t", "trigger", error_workflow=handler), node("f", "fail", message="boom")], [edge("t", "f")])
    res = dry(client, wid)
    assert res["status"] == "failed" and client.get("/api/executions", params={"workflow_id": handler}).json() == []
    client.post(f"/api/workflows/{wid}/run")  # a real run does call it
    assert len(client.get("/api/executions", params={"workflow_id": handler}).json()) == 1


def test_a_dry_run_is_stored_as_its_own_kind_and_does_not_stick(client, fake_http):
    wid = build(client, node("h", "http", method="DELETE", url="https://api.example.com/x"))
    dry(client, wid)
    kinds = [r["trigger"] for r in client.get("/api/executions", params={"workflow_id": wid}).json()]
    assert kinds == ["dry"]
    real = client.post(f"/api/workflows/{wid}/run").json()  # the next real run is not affected
    assert real["status"] == "success" and len(fake_http.calls) == 1 and "dry_run" not in real


def test_sub_workflows_and_each_item_mode_are_dry_too(client, fake_http):
    child = build(client, node("h", "http", method="POST", url="https://api.example.com/child"), name="Child")
    wid = build(client, node("s", "subflow", workflow_id=child))
    res = dry(client, wid)
    assert res["status"] == "success" and fake_http.calls == []
    items = make_flow(client, [node("t", "trigger"), node("i", "input", json='[{"n": 1}, {"n": 2}]'), node("h", "http", method="POST", url="https://api.example.com/{{n}}", each_item="true")], [edge("t", "i"), edge("i", "h")])
    res = dry(client, items)
    assert res["status"] == "success" and fake_http.calls == [] and len(res["not_done"]) == 2


def test_app_actions_split_into_reads_and_writes(client, fake_http):
    client.put("/api/credentials/SLK", json={"value": "xoxb-test-token-12345"})
    fake_http.reply["response"] = FakeResponse(200, {"ok": True, "channels": []})
    write = dry(client, build(client, node("a", "app", service="slack", operation="post_message", credential="SLK", fields='{"channel": "#x", "text": "hi"}')))
    assert fake_http.calls == [] and write["not_done"][0]["not_done"] == "slack post_message"
    read = dry(client, build(client, node("a", "app", service="slack", operation="list_channels", credential="SLK", fields="{}")))
    assert len(fake_http.calls) == 1 and read["not_done"] == []


def test_dry_and_live_cannot_be_combined_and_viewers_cannot_dry_run(client):
    wid = build(client)
    assert client.post(f"/api/workflows/{wid}/run?dry=true&live=true").status_code == 400
    client.post("/api/users", json={"username": "dry-viewer", "password": "long-enough-pw", "role": "viewer"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "dry-viewer", "password": "long-enough-pw"}).status_code == 200
    assert c.post(f"/api/workflows/{wid}/run?dry=true").status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'dry-viewer')}")


def test_parallel_branches_are_dry_too(client, fake_http):
    nodes = [node("t", "trigger"), node("i", "input", json='{"x": 1}'), node("a", "http", method="POST", url="https://api.example.com/a"), node("b", "http", method="POST", url="https://api.example.com/b"), node("o1", "output"), node("o2", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "a"), edge("i", "b"), edge("a", "o1"), edge("b", "o2")])
    res = dry(client, wid)
    assert res["status"] == "success" and fake_http.calls == [] and len(res["not_done"]) == 2
