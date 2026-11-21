import json

import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import FakeResponse, edge, make_flow, node

AI = "http://127.0.0.1:11434/v1?model=llama3.2&key=sk-explain-secret-1"


@pytest.fixture(autouse=True)
def _env(clean_state, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")


def failing(client, fake_http, url="https://api.example.com/items/{{id}}"):
    fake_http.reply["response"] = FakeResponse(500, {"error": "down"})
    nodes = [node("t", "trigger"), node("i", "input", json='{"id": 7}'), node("h", "http", url=url, method="GET"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "h"), edge("h", "o")])
    return wid, client.post(f"/api/workflows/{wid}/run").json()


# ------------------------------------------------------------ replay with edited input
def test_replay_can_use_edited_input(client, fake_http):
    wid, run = failing(client, fake_http)
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    res = client.post(f"/api/executions/{run['id']}/replay", json={"input": {"id": 99}}).json()
    assert res["status"] == "success" and res["edited_input"] is True and fake_http.calls[-1]["url"].endswith("/items/99")


def test_replay_without_a_body_still_uses_the_original_input(client, fake_http):
    wid, run = failing(client, fake_http)
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    res = client.post(f"/api/executions/{run['id']}/replay").json()
    assert res["edited_input"] is False and fake_http.calls[-1]["url"].endswith("/items/7")


def test_edited_input_may_be_any_json_but_not_huge(client, fake_http):
    wid, run = failing(client, fake_http)
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    assert client.post(f"/api/executions/{run['id']}/replay", json={"input": [1, 2]}).status_code == 200
    assert client.post(f"/api/executions/{run['id']}/replay", json={"input": "x" * 300_000}).status_code == 400


def test_a_too_large_original_can_be_replayed_with_edited_input(client, fake_http):
    wid, run = failing(client, fake_http)
    steps = json.loads(json.dumps(run["steps"]))
    for s in steps:
        if s["node_id"] == "h": s["input"] = {"truncated": True, "preview": "..."}
    with main.conn() as db: db.execute("UPDATE executions SET steps=? WHERE id=?", (json.dumps(steps), run["id"]))
    assert client.post(f"/api/executions/{run['id']}/replay").status_code == 400
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    assert client.post(f"/api/executions/{run['id']}/replay", json={"input": {"id": 1}}).json()["status"] == "success"


# ------------------------------------------------------------ explain a failed run
def model(monkeypatch, reply="The server answered 500. Check the URL."):
    asked = []

    def fake_request(method, url, headers, **kwargs):
        asked.append(kwargs["json"]["messages"])
        return {"choices": [{"message": {"content": reply}}]}
    monkeypatch.setattr(main, "do_request", fake_request)
    return asked


def test_a_failed_run_is_explained_from_its_facts(client, fake_http, monkeypatch):
    wid, run = failing(client, fake_http)
    client.put("/api/credentials/LLM", json={"value": AI})
    asked = model(monkeypatch)
    res = client.post(f"/api/executions/{run['id']}/explain", json={"credential": "LLM"})
    assert res.status_code == 200 and res.json()["explanation"].startswith("The server answered 500") and res.json()["node_id"] == "h"
    sent = asked[0][1]["content"]
    assert "items/{{id}}" in sent and "500" in sent


def test_the_explanation_never_gets_secrets(client, fake_http, monkeypatch):
    client.put("/api/credentials/APIKEY", json={"value": "super-secret-token-77"})
    wid, run = failing(client, fake_http, url="https://api.example.com/x?k={{cred.APIKEY}}")
    client.put("/api/credentials/LLM", json={"value": AI})
    asked = model(monkeypatch, "Your key is super-secret-token-77")
    res = client.post(f"/api/executions/{run['id']}/explain", json={"credential": "LLM"})
    assert "super-secret-token-77" not in json.dumps(asked) and "sk-explain-secret-1" not in json.dumps(asked)
    assert "super-secret-token-77" not in res.text  # even if the model repeats one


def test_only_failed_runs_can_be_explained_and_the_credential_must_exist(client, fake_http, monkeypatch):
    ok = client.post(f"/api/workflows/{make_flow(client, [node('t', 'trigger'), node('o', 'output')], [edge('t', 'o')])}/run").json()
    client.put("/api/credentials/LLM", json={"value": AI})
    model(monkeypatch)
    assert client.post(f"/api/executions/{ok['id']}/explain", json={"credential": "LLM"}).status_code == 400
    assert client.post("/api/executions/nope/explain", json={"credential": "LLM"}).status_code == 404
    wid, run = failing(client, fake_http)
    assert client.post(f"/api/executions/{run['id']}/explain", json={"credential": "MISSING"}).status_code == 400


def test_an_unreachable_model_is_a_502(client, fake_http, monkeypatch):
    import httpx
    wid, run = failing(client, fake_http)
    client.put("/api/credentials/LLM", json={"value": AI})

    def boom(*a, **k): raise httpx.ConnectError("refused")
    monkeypatch.setattr(main, "do_request", boom)
    res = client.post(f"/api/executions/{run['id']}/explain", json={"credential": "LLM"})
    assert res.status_code == 502 and "sk-explain-secret-1" not in res.text


def test_a_viewer_cannot_explain(client, fake_http):
    wid, run = failing(client, fake_http)
    client.post("/api/users", json={"username": "explain-viewer", "password": "long-enough-pw", "role": "viewer"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "explain-viewer", "password": "long-enough-pw"}).status_code == 200
    assert c.post(f"/api/executions/{run['id']}/explain", json={"credential": "LLM"}).status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'explain-viewer')}")


# ------------------------------------------------------------ version diff
def save(client, wid, nodes, edges, name="Diffed"):
    assert client.put(f"/api/workflows/{wid}", json={"name": name, "description": "", "nodes": nodes, "edges": edges}).status_code == 200


def test_a_version_is_compared_with_the_current_workflow(client):
    wid = make_flow(client, [node("t", "trigger"), node("a", "set", json='{"x": 1}'), node("o", "output")], [edge("t", "a"), edge("a", "o")], name="Diffed")
    save(client, wid, [node("t", "trigger"), node("a", "set", json='{"x": 2}'), node("n", "template", template="hi"), node("o", "output")],
         [edge("t", "a"), edge("a", "n"), edge("n", "o")], name="Renamed")
    version = client.get(f"/api/workflows/{wid}/versions").json()[0]["id"]
    d = client.get(f"/api/workflows/{wid}/versions/{version}/diff").json()
    assert d["name"] == {"before": "Diffed", "after": "Renamed"}
    assert d["nodes_added"] == [{"id": "n", "type": "template"}] and d["nodes_removed"] == []
    assert d["nodes_changed"] == [{"id": "a", "type": "set", "fields": [{"field": "json", "before": '{"x": 1}', "after": '{"x": 2}'}]}]
    assert {"from": "a", "to": "n"} in d["edges_added"] and {"from": "a", "to": "o"} in d["edges_removed"]


def test_two_versions_can_be_compared_and_identical_ones_show_nothing(client):
    wid = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="Same")
    save(client, wid, [node("t", "trigger"), node("o", "output"), node("x", "output")], [edge("t", "o"), edge("t", "x")], name="Same")
    save(client, wid, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="Same")
    ids = [v["id"] for v in client.get(f"/api/workflows/{wid}/versions").json()]
    d = client.get(f"/api/workflows/{wid}/versions/{ids[-1]}/diff", params={"against": ids[0]}).json()
    assert d["nodes_removed"] == [{"id": "x", "type": "output"}] or d["nodes_added"] == [{"id": "x", "type": "output"}]
    same = client.get(f"/api/workflows/{wid}/versions/{ids[-1]}/diff", params={"against": ids[-1]}).json()
    assert same["nodes_added"] == same["nodes_removed"] == same["nodes_changed"] == same["edges_added"] == same["edges_removed"] == [] and same["name"] is None


def test_diff_errors(client):
    wid = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")])
    assert client.get(f"/api/workflows/{wid}/versions/99999/diff").status_code == 404
    save(client, wid, [node("t", "trigger"), node("o", "output"), node("x", "output")], [edge("t", "o"), edge("t", "x")])
    v = client.get(f"/api/workflows/{wid}/versions").json()[0]["id"]
    assert client.get(f"/api/workflows/{wid}/versions/{v}/diff", params={"against": "junk"}).status_code == 400
    assert client.get(f"/api/workflows/{wid}/versions/{v}/diff", params={"against": 99999}).status_code == 404


# ------------------------------------------------------------ AI call limit
def ai_flow(client, calls, limit):
    nodes = [node("t", "trigger"), node("i", "input", json='{"text": "hello"}')]
    nodes += [node(f"a{k}", "ai", credential="LLM", task="summarize", prompt="{{text}}", length="short") for k in range(calls)] + [node("o", "output")]
    chain = ["t", "i", *[f"a{k}" for k in range(calls)], "o"]
    wid = make_flow(client, nodes, [edge(a, b) for a, b in zip(chain, chain[1:])])
    doc = client.get(f"/api/workflows/{wid}").json()
    assert client.put(f"/api/workflows/{wid}", json={"name": doc["name"], "description": "", "nodes": doc["nodes"], "edges": doc["edges"], "settings": {"ai_call_limit": limit}}).status_code == 200
    return wid


def test_the_ai_call_limit_stops_a_run(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    asked = model(monkeypatch, "short summary")
    wid = ai_flow(client, 4, 2)
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "limit of 2 AI calls" in res["error"] and len(asked) == 2


def test_a_run_within_the_limit_is_fine_and_each_run_starts_fresh(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    asked = model(monkeypatch, "short summary")
    wid = ai_flow(client, 2, 2)
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success"
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success" and len(asked) == 4


def test_the_limit_is_validated_and_absent_means_unlimited(client, monkeypatch):
    base = {"name": "x", "description": "", "nodes": [node("t", "trigger"), node("o", "output")], "edges": [edge("t", "o")]}
    for bad in ("abc", 501, -3):
        assert client.post("/api/workflows", json={**base, "settings": {"ai_call_limit": bad}}).status_code == 422
    ok = client.post("/api/workflows", json={**base, "settings": {"ai_call_limit": "12"}})
    assert ok.status_code in (200, 201)
    client.put("/api/credentials/LLM", json={"value": AI})
    asked = model(monkeypatch, "s")
    wid = ai_flow(client, 5, 0)  # 0 = no limit
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success" and len(asked) == 5
