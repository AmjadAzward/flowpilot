import pytest
from fastapi.testclient import TestClient

from app import main
from app.make_import import convert
from conftest import FakeResponse


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def mod(id_, module, mapper=None, x=0, **extra):
    return {"id": id_, "module": module, "version": 1, "parameters": {}, "mapper": mapper or {}, "metadata": {"designer": {"x": x, "y": 0}}, **extra}


BLUEPRINT = {"name": "Lead to Slack", "flow": [
    mod(1, "gateway:CustomWebHook", x=0),
    mod(2, "http:ActionSendData", {"url": "https://api.example.com/{{1.id}}", "method": "post", "headers": [{"name": "X-A", "value": "{{1.token}}"}], "data": '{"n": "{{1.name}}"}'}, x=300),
    mod(3, "slack:CreateMessage", {"channel": "#leads", "text": "New {{2.data.name}} and {{1.name}}"}, x=600),
], "metadata": {"version": 1}}


def test_a_linear_scenario_converts_with_references_to_the_previous_module():
    r = convert(BLUEPRINT)
    assert [n["type"] for n in r["nodes"]] == ["webhook", "http", "app"]
    http = r["nodes"][1]["data"]
    assert http["url"] == "https://api.example.com/{{id}}" and http["method"] == "POST" and http["headers"] == '{"X-A": "{{token}}"}' and http["body"] == '{"n": "{{name}}"}'
    assert [(e["source"], e["target"]) for e in r["edges"]] == [("m1", "m2"), ("m2", "m3")]
    slack = r["nodes"][2]["data"]
    assert slack["service"] == "app" or slack["service"] == "slack"
    warnings = " ".join(w["message"] for w in r["warnings"])
    assert "{{1.name}}" in warnings  # module 1 does not feed module 3 directly, so it is reported
    assert r["nodes"][2]["data"]["fields"].count("{{data.name}}") == 1


def test_unknown_modules_router_filters_and_schedule():
    r = convert({"name": "Poll", "flow": [
        mod(1, "rss:TriggerNewItems"),
        mod(2, "builtin:BasicRouter", routes=[{"flow": [mod(3, "util:SetVariable", {"name": "a", "value": "{{1.title}}"}, filter={"name": "x"})]}, {"flow": [mod(4, "util:FunctionSleep", {"duration": 5})]}]),
    ], "metadata": {"scheduling": {"type": "indefinitely", "interval": 900}}})
    types = {n["id"]: n["type"] for n in r["nodes"]}
    assert types["start"] == "schedule" and types["m1"] == "note" and types["m2"] == "note" and types["m3"] == "set" and types["m4"] == "wait"
    start = r["nodes"][0]["data"]
    assert start["minutes"] == "15"
    assert {("m2", "m3"), ("m2", "m4"), ("m1", "m2"), ("start", "m1")} == {(e["source"], e["target"]) for e in r["edges"]}
    assert r["summary"]["placeholders"] == 1
    text = " ".join(w["message"] for w in r["warnings"])
    assert "filter" in text and "router" in text.lower()


def test_bad_input_is_rejected():
    for bad in ({}, {"flow": "x"}, {"flow": []}, [], "x", {"flow": [mod(i, "util:SetVariable") for i in range(61)]}):
        with pytest.raises(ValueError): convert(bad)


def test_odd_values_do_not_crash():
    r = convert({"flow": [None, 5, mod(None, "http:ActionSendData", {"url": 5, "headers": "nope"}, disabled=False), {"module": None, "mapper": None, "metadata": {"designer": {"x": "a"}}}, mod("a b", "email:ActionSendEmail", {"to": [{"email": "a@b.io"}, {"email": "c@d.io"}], "subject": "S"}, disabled=True)]})
    assert len(r["nodes"]) == 4 and r["nodes"][0]["type"] == "trigger"


def test_endpoint_needs_a_login_and_an_editor_role(client):
    assert client.post("/api/import/make", json={"nope": 1}).status_code == 400
    ok = client.post("/api/import/make", json=BLUEPRINT)
    assert ok.status_code == 200 and ok.json()["summary"]["nodes"] == 3
    assert TestClient(main.app).post("/api/import/make", json=BLUEPRINT).status_code in (401, 403)


def test_an_imported_scenario_can_be_saved_and_run(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    r = client.post("/api/import/make", json={"name": "Calls", "flow": [mod(1, "util:SetVariable", {"name": "id", "value": "7"}), mod(2, "http:ActionSendData", {"url": "https://api.example.com/{{1.id}}", "method": "get"})]}).json()
    wid = client.post("/api/workflows", json={"name": r["name"], "description": r["description"], "nodes": r["nodes"], "edges": r["edges"]}).json()["id"]
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success", res.get("error")
    assert fake_http.calls[-1]["url"] == "https://api.example.com/7"


def test_simple_route_filters_become_condition_nodes():
    flt = {"name": "paid", "conditions": [[{"a": "{{1.status}}", "b": "paid", "o": "text:equal"}]]}
    r = convert({"flow": [mod(1, "gateway:CustomWebHook"), mod(2, "builtin:BasicRouter", routes=[
        {"flow": [mod(3, "util:SetVariable", {"name": "a", "value": "1"}, filter=flt)]},
        {"flow": [mod(4, "util:SetVariable", {"name": "b", "value": "1"}, filter={"conditions": [[{"a": "{{9.x}}", "b": "y", "o": "text:equal"}]]})]}]) ]})
    cond = next(n for n in r["nodes"] if n["id"] == "m3_if")
    assert cond["type"] == "condition" and cond["data"]["path"] == "status" and cond["data"]["op"] == "equals" and cond["data"]["equals"] == "paid"
    edges = {(e["source"], e["target"], e.get("sourceHandle")) for e in r["edges"]}
    assert ("m2", "m3_if", None) in edges and ("m3_if", "m3", "true") in edges and ("m2", "m4", None) in edges
    assert not any(n["id"] == "m4_if" for n in r["nodes"])
    assert any("not a single plain comparison" in w["message"] for w in r["warnings"] if w["node"].endswith("4"))


def test_a_filtered_import_runs(client):
    flt = {"conditions": [[{"a": "{{1.n}}", "b": "5", "o": "number:greater"}]]}
    r = client.post("/api/import/make", json={"name": "F", "flow": [mod(1, "util:SetVariable", {"name": "n", "value": "9"}), mod(2, "util:SetVariable", {"name": "big", "value": "yes"}, filter=flt)]}).json()
    wid = client.post("/api/workflows", json={"name": "F", "description": "", "nodes": r["nodes"], "edges": r["edges"]}).json()["id"]
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success"
