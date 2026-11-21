import json

import pytest

from app import main
from conftest import edge, make_flow, node

AI = "http://127.0.0.1:11434/v1?model=llama3.2&key=sk-usage-secret-1"


@pytest.fixture(autouse=True)
def _env(clean_state, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")
    with main.conn() as db:
        db.execute("DELETE FROM workflow_tests")
        db.execute("DELETE FROM executions")


def model(monkeypatch, tokens=100):
    def fake_request(method, url, headers, **kwargs):
        return {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": tokens}}
    monkeypatch.setattr(main, "do_request", fake_request)


def ai_flow(client, calls, name="Usage flow"):
    nodes = [node("t", "trigger"), node("i", "input", json='{"text": "hello"}')]
    nodes += [node(f"a{k}", "ai", credential="LLM", task="summarize", prompt="{{text}}", length="short") for k in range(calls)] + [node("o", "output")]
    chain = ["t", "i", *[f"a{k}" for k in range(calls)], "o"]
    return make_flow(client, nodes, [edge(a, b) for a, b in zip(chain, chain[1:])], name=name)


# ------------------------------------------------------------ AI usage per run
def test_a_run_reports_and_stores_what_the_ai_used(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    model(monkeypatch, tokens=120)
    res = client.post(f"/api/workflows/{ai_flow(client, 3)}/run").json()
    assert res["ai_usage"] == {"calls": 3, "tokens": 360}
    listed = client.get("/api/executions").json()
    assert listed[0]["ai_usage"] == {"calls": 3, "tokens": 360}


def test_runs_without_ai_have_no_usage(client):
    wid = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert "ai_usage" not in res and client.get("/api/executions").json()[0]["ai_usage"] is None


def test_a_sub_workflow_counts_towards_its_caller(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    model(monkeypatch, tokens=50)
    child = ai_flow(client, 2, name="Child")
    parent = make_flow(client, [node("t", "trigger"), node("s", "subflow", workflow_id=child), node("o", "output")], [edge("t", "s"), edge("s", "o")], name="Parent")
    assert client.post(f"/api/workflows/{parent}/run").json()["ai_usage"] == {"calls": 2, "tokens": 100}


def test_usage_is_counted_even_when_the_run_fails(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    model(monkeypatch, tokens=10)
    wid = ai_flow(client, 3)
    doc = client.get(f"/api/workflows/{wid}").json()
    client.put(f"/api/workflows/{wid}", json={"name": doc["name"], "description": "", "nodes": doc["nodes"], "edges": doc["edges"], "settings": {"ai_call_limit": 2}})
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and res["ai_usage"]["calls"] == 2


def test_stats_add_up_the_ai_usage_in_the_window(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    model(monkeypatch, tokens=100)
    wid = ai_flow(client, 2)
    client.post(f"/api/workflows/{wid}/run")
    client.post(f"/api/workflows/{wid}/run")
    stats = client.get("/api/stats", params={"window": "24h"}).json()
    assert stats["ai_calls"] == 4 and stats["ai_tokens"] == 400


def test_usage_never_exposes_the_ai_key(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    model(monkeypatch)
    res = client.post(f"/api/workflows/{ai_flow(client, 1)}/run")
    assert "sk-usage-secret-1" not in res.text and "sk-usage-secret-1" not in json.dumps(client.get("/api/executions").json())


# ------------------------------------------------------------ backup carries the saved tests
def greeter(client, name):
    return make_flow(client, [node("t", "trigger"), node("g", "set", json='{"greeting": "Hello {{name}}"}'), node("o", "output")], [edge("t", "g"), edge("g", "o")], name=name)


def test_a_backup_includes_saved_tests_and_a_restore_brings_them_back(client):
    wid = greeter(client, "Backed up greeter")
    client.post(f"/api/workflows/{wid}/tests", json={"name": "greets Ada", "input": {"name": "Ada"}, "expected": {"greeting": "Hello Ada"}})
    client.post(f"/api/workflows/{wid}/tests", json={"name": "fails", "expect": "failed", "expected": "boom"})
    backup = client.get("/api/backup/workflows").json()
    item = next(w for w in backup["workflows"] if w["name"] == "Backed up greeter")
    assert {t["name"] for t in item["tests"]} == {"greets Ada", "fails"}
    res = client.post("/api/backup/restore", json={"format": "flowpilot-backup", "workflows": [item]}).json()
    assert res["created"][0]["tests"] == 2
    new_id = res["created"][0]["id"]
    run = client.post(f"/api/workflows/{new_id}/tests/run").json()
    assert {r["name"]: r["passed"] for r in run["results"]} == {"greets Ada": True, "fails": False}  # the restored test really runs


def test_bad_tests_in_a_backup_are_dropped_without_losing_the_workflow(client):
    item = {"name": "Odd backup", "description": "", "nodes": [node("t", "trigger"), node("o", "output")], "edges": [edge("t", "o")], "tags": [], "settings": {},
            "tests": [{"name": "good", "expected": {"a": 1}}, {"name": "", "expected": 1}, {"name": "bad mode", "mode": "regex"}, "junk", {"name": "good"}]}
    res = client.post("/api/backup/restore", json={"format": "flowpilot-backup", "workflows": [item]}).json()
    assert res["created"][0]["tests"] == 1 and res["skipped"] == []
    assert [t["name"] for t in client.get(f"/api/workflows/{res['created'][0]['id']}/tests").json()] == ["good"]


def test_old_backups_without_tests_still_restore(client):
    item = {"name": "Old format", "description": "", "nodes": [node("t", "trigger"), node("o", "output")], "edges": [edge("t", "o")]}
    res = client.post("/api/backup/restore", json={"format": "flowpilot-backup", "workflows": [item]}).json()
    assert res["created"][0]["tests"] == 0


def test_a_duplicate_keeps_the_tests(client):
    wid = greeter(client, "Original greeter")
    client.post(f"/api/workflows/{wid}/tests", json={"name": "greets Ada", "input": {"name": "Ada"}, "expected": {"greeting": "Hello Ada"}})
    copy = client.post(f"/api/workflows/{wid}/duplicate").json()["id"]
    assert [t["name"] for t in client.get(f"/api/workflows/{copy}/tests").json()] == ["greets Ada"]
    assert len(client.get(f"/api/workflows/{wid}/tests").json()) == 1  # the original is untouched
