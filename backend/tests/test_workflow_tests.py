import json
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import FakeResponse, edge, make_flow, node

AI = "http://127.0.0.1:11434/v1?model=llama3.2&key=sk-budget-secret-1"


@pytest.fixture(autouse=True)
def _env(clean_state, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")
    with main.conn() as db: db.execute("DELETE FROM workflow_tests")


def greeter(client, name="Greeter"):
    nodes = [node("t", "trigger"), node("g", "set", json='{"greeting": "Hello {{name}}", "n": 1}'), node("o", "output")]
    return make_flow(client, nodes, [edge("t", "g"), edge("g", "o")], name=name)


def add(client, wid, **body):
    return client.post(f"/api/workflows/{wid}/tests", json=body)


# ------------------------------------------------------------ saved test cases
def test_a_saved_test_passes_and_a_changed_workflow_makes_it_fail(client):
    wid = greeter(client)
    assert add(client, wid, name="greets Ada", input={"name": "Ada"}, expected={"greeting": "Hello Ada"}).status_code == 201
    res = client.post(f"/api/workflows/{wid}/tests/run").json()
    assert res["passed"] == 1 and res["failed"] == 0 and res["results"][0]["passed"] is True
    doc = client.get(f"/api/workflows/{wid}").json()
    nodes = [{**n, "data": {**n["data"], "json": '{"greeting": "Bye {{name}}"}'}} if n["id"] == "g" else n for n in doc["nodes"]]
    client.put(f"/api/workflows/{wid}", json={"name": doc["name"], "description": "", "nodes": nodes, "edges": doc["edges"]})
    res = client.post(f"/api/workflows/{wid}/tests/run").json()
    assert res["failed"] == 1 and res["results"][0]["actual"]["greeting"] == "Bye Ada"


def test_contains_ignores_extra_fields_but_equals_does_not(client):
    wid = greeter(client)
    add(client, wid, name="subset", input={"name": "Ada"}, expected={"n": 1}, mode="contains")
    add(client, wid, name="exact", input={"name": "Ada"}, expected={"n": 1}, mode="equals")
    by = {r["name"]: r["passed"] for r in client.post(f"/api/workflows/{wid}/tests/run").json()["results"]}
    assert by == {"subset": True, "exact": False}


def test_a_test_may_expect_a_failure(client):
    wid = make_flow(client, [node("t", "trigger"), node("f", "fail", message="no stock left")], [edge("t", "f")])
    add(client, wid, name="fails as planned", expect="failed", expected="NO STOCK")
    add(client, wid, name="wrong message", expect="failed", expected="payment")
    add(client, wid, name="expects success", expected=None)
    by = {r["name"]: r["passed"] for r in client.post(f"/api/workflows/{wid}/tests/run").json()["results"]}
    assert by == {"fails as planned": True, "wrong message": False, "expects success": False}


def test_tests_run_as_a_dry_run_so_nothing_is_sent(client, fake_http):
    nodes = [node("t", "trigger"), node("h", "http", method="POST", url="https://api.example.com/hook", body="{}"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "h"), edge("h", "o")])
    add(client, wid, name="posts", input={"a": 1}, expected={"a": 1})
    res = client.post(f"/api/workflows/{wid}/tests/run").json()
    assert res["passed"] == 1 and fake_http.calls == []
    assert client.get("/api/executions", params={"workflow_id": wid}).json() == []  # tests leave no run history


def test_credentials_never_show_in_test_results(client, fake_http):
    client.put("/api/credentials/APIKEY", json={"value": "super-secret-token-55"})
    wid = make_flow(client, [node("t", "trigger"), node("s", "set", json='{"key": "{{cred.APIKEY}}"}'), node("o", "output")], [edge("t", "s"), edge("s", "o")])
    add(client, wid, name="leaks?", expected={"key": "nope"})
    assert "super-secret-token-55" not in client.post(f"/api/workflows/{wid}/tests/run").text


def test_test_names_are_unique_limited_and_deleted_with_the_workflow(client):
    wid = greeter(client)
    first = add(client, wid, name="one", expected={"n": 1}).json()
    assert add(client, wid, name="one").status_code == 400
    assert add(client, wid, name="").status_code == 422
    assert add(client, wid, name="bad mode", mode="regex").status_code == 422
    assert len(client.get(f"/api/workflows/{wid}/tests").json()) == 1
    assert client.delete(f"/api/workflows/{wid}/tests/{first['id']}").status_code == 204
    assert client.get(f"/api/workflows/{wid}/tests").json() == []
    add(client, wid, name="again")
    client.delete(f"/api/workflows/{wid}")
    with main.conn() as db: assert db.execute("SELECT COUNT(*) c FROM workflow_tests WHERE workflow_id=?", (wid,)).fetchone()["c"] == 0
    assert client.get("/api/workflows/nope/tests").status_code == 404


def test_viewers_can_read_but_not_change_or_run_tests(client):
    wid = greeter(client)
    add(client, wid, name="t1", expected={"n": 1})
    client.post("/api/users", json={"username": "tests-viewer", "password": "long-enough-pw", "role": "viewer"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "tests-viewer", "password": "long-enough-pw"}).status_code == 200
    assert c.get(f"/api/workflows/{wid}/tests").status_code == 200
    assert c.post(f"/api/workflows/{wid}/tests", json={"name": "x"}).status_code == 403
    assert c.post(f"/api/workflows/{wid}/tests/run").status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'tests-viewer')}")


# ------------------------------------------------------------ bulk replay
def failing(client, fake_http, name):
    nodes = [node("t", "trigger"), node("i", "input", json='{"id": 7}'), node("h", "http", url="https://api.example.com/items/{{id}}", method="GET"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "h"), edge("h", "o")], name=name)
    fake_http.reply["response"] = FakeResponse(500, {"error": "down"})
    return wid, client.post(f"/api/workflows/{wid}/run").json()


def test_every_recent_failure_is_replayed_once(client, fake_http):
    a, run_a = failing(client, fake_http, "Bulk A")
    b, run_b = failing(client, fake_http, "Bulk B")
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    res = client.post("/api/executions/replay-failed", json={}).json()
    assert res["found"] == 2 and res["succeeded"] == 2 and res["still_failing"] == 0
    assert {r["id"] for r in res["results"]} == {run_a["id"], run_b["id"]}
    again = client.post("/api/executions/replay-failed", json={}).json()
    assert again["found"] == 0  # already replayed, so nothing is repeated


def test_a_run_that_fails_again_can_be_retried_later(client, fake_http):
    wid, run = failing(client, fake_http, "Bulk C")
    res = client.post("/api/executions/replay-failed", json={"workflow_id": wid}).json()
    assert res["found"] == 1 and res["still_failing"] == 1
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    res = client.post("/api/executions/replay-failed", json={"workflow_id": wid}).json()
    assert res["found"] == 1 and res["succeeded"] == 1  # the failed replay is now the one retried


def test_bulk_replay_can_be_limited_by_workflow_age_and_count(client, fake_http):
    a, _ = failing(client, fake_http, "Bulk D")
    b, _ = failing(client, fake_http, "Bulk E")
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    assert client.post("/api/executions/replay-failed", json={"workflow_id": a, "limit": 1}).json()["found"] == 1
    with main.conn() as db: db.execute("UPDATE executions SET started_at='2020-01-01T00:00:00+00:00' WHERE workflow_id=?", (b,))
    assert client.post("/api/executions/replay-failed", json={"workflow_id": b, "since_minutes": 60}).json()["found"] == 0
    assert client.post("/api/executions/replay-failed", json={"since_minutes": 0}).status_code == 422
    assert client.post("/api/executions/replay-failed", json={"limit": 1000}).status_code == 422


def test_runs_that_cannot_be_replayed_are_reported_not_hidden(client):
    nodes = [node("t", "trigger"), node("i", "input", json="[1, 2]"), node("l", "loop", path=""), node("f", "fail", message="inside"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "l"), edge("l", "f", "each"), edge("l", "o", "done")])
    client.post(f"/api/workflows/{wid}/run")
    res = client.post("/api/executions/replay-failed", json={"workflow_id": wid}).json()
    assert res["found"] == 1 and res["skipped"] == 1 and "loop" in res["results"][0]["error"]


def test_dry_runs_are_not_bulk_replayed_and_viewers_cannot_bulk_replay(client, fake_http):
    wid = make_flow(client, [node("t", "trigger"), node("f", "fail", message="x")], [edge("t", "f")])
    client.post(f"/api/workflows/{wid}/run?dry=true")
    assert client.post("/api/executions/replay-failed", json={"workflow_id": wid}).json()["found"] == 0
    client.post("/api/users", json={"username": "bulk-viewer", "password": "long-enough-pw", "role": "viewer"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "bulk-viewer", "password": "long-enough-pw"}).status_code == 200
    assert c.post("/api/executions/replay-failed", json={}).status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'bulk-viewer')}")


# ------------------------------------------------------------ AI token limit
def model(monkeypatch, tokens=None, reply="ok"):
    asked = []

    def fake_request(method, url, headers, **kwargs):
        asked.append(1)
        out = {"choices": [{"message": {"content": reply}}]}
        if tokens is not None: out["usage"] = {"total_tokens": tokens}
        return out
    monkeypatch.setattr(main, "do_request", fake_request)
    return asked


def ai_flow(client, calls, settings):
    nodes = [node("t", "trigger"), node("i", "input", json='{"text": "hello"}')]
    nodes += [node(f"a{k}", "ai", credential="LLM", task="summarize", prompt="{{text}}", length="short") for k in range(calls)] + [node("o", "output")]
    chain = ["t", "i", *[f"a{k}" for k in range(calls)], "o"]
    wid = make_flow(client, nodes, [edge(a, b) for a, b in zip(chain, chain[1:])])
    doc = client.get(f"/api/workflows/{wid}").json()
    assert client.put(f"/api/workflows/{wid}", json={"name": doc["name"], "description": "", "nodes": doc["nodes"], "edges": doc["edges"], "settings": settings}).status_code == 200
    return wid


def test_the_token_limit_uses_the_tokens_the_service_reports(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    asked = model(monkeypatch, tokens=600)
    res = client.post(f"/api/workflows/{ai_flow(client, 4, {'ai_token_limit': 1000})}/run").json()
    assert res["status"] == "failed" and "1000 AI tokens" in res["error"] and len(asked) == 2  # 0 -> 600 -> 1200, then refused


def test_without_a_usage_report_the_tokens_are_estimated(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    asked = model(monkeypatch, reply="x" * 6000)  # ~1500 tokens per call
    res = client.post(f"/api/workflows/{ai_flow(client, 3, {'ai_token_limit': 1000})}/run").json()
    assert res["status"] == "failed" and len(asked) == 1


def test_calls_and_tokens_limits_work_together_and_are_validated(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    asked = model(monkeypatch, tokens=10)
    assert client.post(f"/api/workflows/{ai_flow(client, 5, {'ai_token_limit': 5000, 'ai_call_limit': 2})}/run").json()["status"] == "failed" and len(asked) == 2
    base = {"name": "x", "description": "", "nodes": [node("t", "trigger"), node("o", "output")], "edges": [edge("t", "o")]}
    for bad in ("abc", 999, 6_000_000):
        assert client.post("/api/workflows", json={**base, "settings": {"ai_token_limit": bad}}).status_code == 422
    assert client.post("/api/workflows", json={**base, "settings": {"ai_token_limit": "2500"}}).status_code in (200, 201)


# ------------------------------------------------------------ per-step time limit
def slow(client, **data):
    wid = make_flow(client, [node("t", "trigger"), node("d", "delay", seconds="2", **data), node("o", "output")], [edge("t", "d"), edge("d", "o")])
    t0 = time.perf_counter()
    res = client.post(f"/api/workflows/{wid}/run").json()
    return res, time.perf_counter() - t0


def test_a_slow_step_is_cut_off_at_its_own_limit(client):
    res, took = slow(client, step_timeout="1")
    assert res["status"] == "failed" and "longer than its limit of 1 seconds" in res["error"] and took < 1.9


def test_a_step_within_its_limit_is_untouched(client):
    res, took = slow(client, step_timeout="5")
    assert res["status"] == "success" and took >= 1.9


def test_a_timed_out_step_can_continue_on_error(client):
    res, _ = slow(client, step_timeout="1", on_error="continue")
    assert res["status"] == "success" and "longer than its limit" in json.dumps(res["steps"])


def test_a_bad_step_limit_is_a_clear_error(client):
    for bad in ("abc", "9999", "-1"):
        res, _ = slow(client, step_timeout=bad)
        assert res["status"] == "failed" and "step time limit" in res["error"]
