import json

import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import FakeResponse, edge, make_flow, node

AI = "http://127.0.0.1:11434/v1?model=llama3.2&key=sk-builder-secret-1"

GOOD = {
    "name": "Welcome new signups",
    "description": "Greets people who sign up.",
    "nodes": [
        {"id": "start", "type": "webhook", "label": "Signup", "data": {"sample": {"email": "ada@example.com", "name": "Ada"}, "credential": "SHOULD_BE_DROPPED"}},
        {"id": "msg", "type": "template", "label": "Greeting", "data": {"template": "Welcome {{body.name}}!"}},
        {"id": "mail", "type": "email", "label": "Send", "data": {"to": "{{body.email}}", "subject": "Hi", "body": "{{data}}", "auth_credential": "ALSO_DROPPED", "credential": "SMTP"}},
        {"id": "done", "type": "output", "label": "Done", "data": {}},
    ],
    "edges": [{"source": "start", "target": "msg"}, {"source": "msg", "target": "mail"}, {"source": "mail", "target": "done"}],
}


@pytest.fixture(autouse=True)
def _env(clean_state, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")
    client_state = {}
    yield client_state


def model(monkeypatch, *answers):
    """Make the AI service answer with each reply in turn; records what it was asked."""
    asked, it = [], iter(answers)

    def fake_request(method, url, headers, **kwargs):
        asked.append(kwargs["json"]["messages"])
        reply = next(it)
        return {"choices": [{"message": {"content": reply if isinstance(reply, str) else json.dumps(reply)}}]}
    monkeypatch.setattr(main, "do_request", fake_request)
    return asked


def build(client, description="When someone signs up, email them a welcome message", cred="LLM"):
    client.put("/api/credentials/LLM", json={"value": AI})
    return client.post("/api/ai/build-workflow", json={"description": description, "credential": cred})


def saved(client, wid):
    return client.get(f"/api/workflows/{wid}").json()


def test_a_description_becomes_an_inactive_checked_draft(client, monkeypatch):
    asked = model(monkeypatch, GOOD)
    res = build(client)
    assert res.status_code == 200 and res.json()["nodes"] == 4
    wf = saved(client, res.json()["id"])
    assert wf["name"] == "Welcome new signups" and wf["active"] is False and "ai-draft" in wf["tags"]
    kinds = {n["id"]: n["type"] for n in wf["nodes"]}
    assert kinds == {"start": "webhook", "msg": "template", "mail": "email", "done": "output"}
    assert "available node types" in asked[0][0]["content"].lower() and asked[0][1]["content"].startswith("When someone signs up")


def test_credentials_and_secrets_are_never_taken_from_the_model(client, monkeypatch):
    model(monkeypatch, GOOD)
    wf = saved(client, build(client).json()["id"])
    blob = json.dumps(wf)
    assert "SHOULD_BE_DROPPED" not in blob and "ALSO_DROPPED" not in blob and '"credential"' not in blob
    assert "sk-builder-secret-1" not in blob  # and the AI key itself never reaches the draft
    mail = next(n for n in wf["nodes"] if n["id"] == "mail")
    assert mail["data"]["to"] == "{{body.email}}"
    start = next(n for n in wf["nodes"] if n["id"] == "start")
    assert json.loads(start["data"]["sample"]) == {"email": "ada@example.com", "name": "Ada"}  # objects become JSON text


def test_nodes_are_laid_out_left_to_right(client, monkeypatch):
    model(monkeypatch, GOOD)
    wf = saved(client, build(client).json()["id"])
    xs = {n["id"]: n["position"]["x"] for n in wf["nodes"]}
    assert xs["start"] < xs["msg"] < xs["mail"] < xs["done"]


def test_a_bad_draft_gets_one_corrective_retry(client, monkeypatch):
    bad = {**GOOD, "nodes": [{**GOOD["nodes"][0]}, {"id": "x", "type": "rm_rf", "data": {}}] + GOOD["nodes"][1:]}
    asked = model(monkeypatch, bad, GOOD)
    res = build(client)
    assert res.status_code == 200 and len(asked) == 2
    assert "rejected" in asked[1][-1]["content"] and "rm_rf" in asked[1][-1]["content"]


def test_two_bad_drafts_create_nothing(client, monkeypatch):
    before = len(client.get("/api/workflows").json())
    model(monkeypatch, {"nodes": []}, "not json at all")
    res = build(client)
    assert res.status_code == 422 and len(client.get("/api/workflows").json()) == before


@pytest.mark.parametrize("change", [
    {"nodes": [{"id": "a", "type": "code", "data": {}}]},
    {"nodes": [{"id": "a", "type": "sql", "data": {}}]},
    {"nodes": [{"id": "a", "type": "file", "data": {}}]},
    {"nodes": [{"id": "a", "type": "trigger"}, {"id": "a", "type": "output"}]},
    {"nodes": [{"id": "a", "type": "trigger"}, {"id": "b", "type": "schedule"}]},
    {"nodes": [{"id": "a", "type": "output"}]},
    {"nodes": [{"id": "a", "type": "trigger"}, {"id": "b", "type": "output"}], "edges": []},
    {"edges": [{"source": "start", "target": "ghost"}]},
    {"nodes": [{"id": "n" + str(i), "type": "template" if i else "trigger", "data": {}} for i in range(30)], "edges": []},
])
def test_unsafe_or_broken_drafts_are_refused(client, monkeypatch, change):
    model(monkeypatch, {**GOOD, **change}, {**GOOD, **change})
    assert build(client).status_code == 422


def test_the_credential_must_exist_and_the_description_must_be_real(client, monkeypatch):
    model(monkeypatch, GOOD)
    assert client.post("/api/ai/build-workflow", json={"description": "A description long enough", "credential": "NOPE"}).status_code == 400
    assert client.post("/api/ai/build-workflow", json={"description": "short", "credential": "LLM"}).status_code == 422


def test_a_draft_never_runs_or_activates_by_itself(client, monkeypatch):
    model(monkeypatch, GOOD)
    wid = build(client).json()["id"]
    assert client.get("/api/executions", params={"workflow_id": wid}).json() == []
    assert saved(client, wid)["active"] is False


def test_viewers_cannot_build(client, monkeypatch):
    model(monkeypatch, GOOD)
    client.put("/api/credentials/LLM", json={"value": AI})
    client.post("/api/users", json={"username": "builder-viewer", "password": "long-enough-pw", "role": "viewer"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "builder-viewer", "password": "long-enough-pw"}).status_code == 200
    assert c.post("/api/ai/build-workflow", json={"description": "A description long enough", "credential": "LLM"}).status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'builder-viewer')}")


def test_a_draft_can_be_tried_with_a_dry_run(client, monkeypatch):
    model(monkeypatch, GOOD)
    wid = build(client).json()["id"]
    res = client.post(f"/api/workflows/{wid}/run?dry=true").json()
    assert res["status"] in ("success", "failed") and res["dry_run"] is True  # the email step is described, never sent
    assert all(a["type"] != "template" for a in res["not_done"])


def test_an_unreachable_model_is_a_clear_error_not_a_crash(client, monkeypatch):
    import httpx

    def boom(*a, **k): raise httpx.ConnectError("refused")
    monkeypatch.setattr(main, "do_request", boom)
    before = len(client.get("/api/workflows").json())
    res = build(client)
    assert res.status_code == 502 and "ConnectError" in res.json()["detail"] and "sk-builder-secret-1" not in res.text
    assert len(client.get("/api/workflows").json()) == before
