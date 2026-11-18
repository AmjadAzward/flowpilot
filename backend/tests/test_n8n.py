import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.n8n_import import convert
from conftest import OWNER


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def n(id_, name, type_, params=None, pos=(0, 0), **extra):
    return {"id": id_, "name": name, "type": f"n8n-nodes-base.{type_}", "typeVersion": 1, "position": list(pos), "parameters": params or {}, **extra}


def link(src_to_targets):
    """{'A': [['B'], ['C']]} -> n8n connections with one output group per list."""
    return {src: {"main": [[{"node": t, "type": "main", "index": 0} for t in group] for group in groups]} for src, groups in src_to_targets.items()}


def types(result): return {x["data"]["label"]: x["type"] for x in result["nodes"]}


def node_by_label(result, label): return next(x for x in result["nodes"] if x["data"]["label"] == label)


def messages(result, node=None): return [w["message"] for w in result["warnings"] if node is None or w["node"] == node]


ORDER_ALERTS = {
    "name": "Order alerts",
    "nodes": [
        n("1", "Webhook", "webhook", {"path": "orders", "httpMethod": "POST"}, (0, 0)),
        n("2", "Edit Fields", "set", {"assignments": {"assignments": [{"id": "a", "name": "total", "value": "={{ $json.body.total }}", "type": "number"}, {"id": "b", "name": "label", "value": "=Order {{ $json.body.id }}", "type": "string"}]}}, (200, 0)),
        n("3", "Big order?", "if", {"conditions": {"conditions": [{"leftValue": "={{ $json.total }}", "rightValue": 100, "operator": {"type": "number", "operation": "gt"}}]}}, (400, 0)),
        n("4", "Notify", "slack", {"resource": "message", "channelId": {"__rl": True, "value": "#alerts", "mode": "name"}, "text": "=Big: {{ $json.label }}"}, (600, -100), credentials={"slackApi": {"id": "1", "name": "Slack"}}),
        n("5", "Log", "noOp", {}, (600, 100)),
    ],
    "connections": link({"Webhook": [["Edit Fields"]], "Edit Fields": [["Big order?"]], "Big order?": [["Notify"], ["Log"]]}),
}


def test_a_typical_workflow_converts_node_by_node():
    r = convert(ORDER_ALERTS)
    assert r["name"] == "Order alerts"
    assert types(r) == {"Webhook": "webhook", "Edit Fields": "set", "Big order?": "condition", "Notify": "app", "Log": "note"}
    setter = json.loads(node_by_label(r, "Edit Fields")["data"]["json"].replace("{{body.total}}", "7").replace("{{body.id}}", "x"))
    assert setter == {"total": 7, "label": "Order x"}  # the number stays a number, the text stays text
    assert "\"total\": {{body.total}}" in node_by_label(r, "Edit Fields")["data"]["json"]
    cond = node_by_label(r, "Big order?")["data"]
    assert (cond["path"], cond["op"], cond["equals"]) == ("total", "gt", "100")
    slack = node_by_label(r, "Notify")["data"]
    assert slack["service"] == "slack" and json.loads(slack["fields"]) == {"channel": "#alerts", "text": "Big: {{label}}"}
    handles = {(e["source"], e["target"]): e.get("sourceHandle") for e in r["edges"]}
    assert handles[("3", "4")] == "true" and handles[("3", "5")] == "false" and handles[("1", "2")] is None


def test_warnings_tell_the_user_what_to_do():
    r = convert(ORDER_ALERTS)
    assert any("webhook address" in m for m in messages(r, "Webhook"))
    assert any("credential" in m.lower() for m in messages(r, "Notify"))
    assert any("slackApi" in m for m in messages(r, "(workflow)"))


def test_the_converted_workflow_can_be_saved_and_run(client):
    flow = {
        "name": "Doubler",
        "nodes": [
            n("m", "Manual", "manualTrigger", {}, (0, 0)),
            n("s", "Edit Fields", "set", {"assignments": {"assignments": [{"name": "total", "value": "={{ $json.total * 2 }}", "type": "number"}, {"name": "who", "value": "={{ $json.name }}", "type": "string"}]}}, (200, 0)),
            n("i", "IF", "if", {"conditions": {"conditions": [{"leftValue": "={{ $json.total }}", "rightValue": 100, "operator": {"type": "number", "operation": "gt"}}]}}, (400, 0)),
            n("t", "Big", "set", {"assignments": {"assignments": [{"name": "msg", "value": "=Big {{ $json.who }}", "type": "string"}]}}, (600, -80)),
            n("f", "Small", "set", {"assignments": {"assignments": [{"name": "msg", "value": "small", "type": "string"}]}}, (600, 80)),
        ],
        "connections": link({"Manual": [["Edit Fields"]], "Edit Fields": [["IF"]], "IF": [["Big"], ["Small"]]}),
    }
    r = client.post("/api/import/n8n", json=flow)
    assert r.status_code == 200, r.text
    body = r.json()
    created = client.post("/api/workflows", json={k: body[k] for k in ("name", "description", "nodes", "edges")})
    assert created.status_code == 201, created.text
    wid = created.json()["id"]
    big = client.post(f"/api/workflows/{wid}/run", json={"input": {"total": 250, "name": "ada"}}).json()
    assert big["status"] == "success" and big["output"] == {"msg": "Big ada"}
    small = client.post(f"/api/workflows/{wid}/run", json={"input": {"total": 10, "name": "bo"}}).json()
    assert small["status"] == "success" and small["output"] == {"msg": "small"}


def test_unknown_nodes_become_pass_through_and_keep_the_shape():
    flow = {"nodes": [n("1", "Start", "manualTrigger"), n("2", "Fancy", "someVendorNode", {"x": 1}), n("3", "Done", "noOp")], "connections": link({"Start": [["Fancy"]], "Fancy": [["Done"]]})}
    r = convert(flow)
    assert types(r)["Fancy"] == "note" and r["summary"]["placeholders"] == 1
    assert any("no FlowPilot equivalent" in m for m in messages(r, "Fancy"))
    assert len(r["edges"]) == 2


def test_disabled_nodes_pass_data_through():
    flow = {"nodes": [n("1", "Start", "manualTrigger"), n("2", "Off", "httpRequest", {"url": "https://x.example.com"}, disabled=True)], "connections": link({"Start": [["Off"]]})}
    r = convert(flow)
    assert types(r)["Off"] == "note" and any("disabled" in m for m in messages(r, "Off"))


def test_schedules():
    def sched(rule): return node_by_label(convert({"nodes": [n("1", "S", "scheduleTrigger", {"rule": {"interval": [rule]}})], "connections": {}}), "S")["data"]
    assert sched({"field": "minutes", "minutesInterval": 15}) == {"label": "S", "mode": "interval", "minutes": "15"}
    assert sched({"field": "hours", "hoursInterval": 2})["minutes"] == "120"
    assert sched({"field": "days", "triggerAtHour": 9, "triggerAtMinute": 5}) == {"label": "S", "mode": "daily", "time": "09:05"}
    assert sched({"field": "cronExpression", "expression": "0 9 * * 1-5"})["cron"] == "0 9 * * 1-5"
    odd = convert({"nodes": [n("1", "S", "scheduleTrigger", {"rule": {"interval": [{"field": "weeks"}]}})], "connections": {}})
    assert node_by_label(odd, "S")["data"]["minutes"] == "60" and messages(odd, "S")
    old = node_by_label(convert({"nodes": [n("1", "C", "cron", {"triggerTimes": {"item": [{"mode": "everyX", "value": 30, "unit": "minutes"}]}})], "connections": {}}), "C")["data"]
    assert old["minutes"] == "30"


def test_a_schedule_converts_to_a_workflow_that_can_be_activated(client):
    r = convert({"nodes": [n("1", "S", "scheduleTrigger", {"rule": {"interval": [{"field": "minutes", "minutesInterval": 10}]}}), n("2", "Do", "noOp")], "connections": link({"S": [["Do"]]})})
    wid = client.post("/api/workflows", json={k: r[k] for k in ("name", "description", "nodes", "edges")}).json()["id"]
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 200


def test_switch_maps_rules_to_numbered_outputs_and_fallback():
    rules = {"values": [{"conditions": {"conditions": [{"leftValue": "={{ $json.kind }}", "rightValue": "a", "operator": {"type": "string", "operation": "equals"}}]}},
                        {"conditions": {"conditions": [{"leftValue": "={{ $json.kind }}", "rightValue": "b", "operator": {"type": "string", "operation": "equals"}}]}}]}
    flow = {"nodes": [n("1", "T", "manualTrigger"), n("2", "Sw", "switch", {"rules": rules}), n("3", "A", "noOp"), n("4", "B", "noOp"), n("5", "Other", "noOp")],
            "connections": link({"T": [["Sw"]], "Sw": [["A"], ["B"], ["Other"]]})}
    r = convert(flow)
    sw = node_by_label(r, "Sw")["data"]
    assert (sw["path"], sw["case1"], sw["case2"]) == ("kind", "a", "b")
    handles = {e["target"]: e.get("sourceHandle") for e in r["edges"] if e["source"] == "2"}
    assert handles == {"3": "1", "4": "2", "5": "3"}


def test_http_request_headers_body_and_auth_note():
    p = {"url": "=https://api.example.com/items/{{ $json.id }}", "method": "POST", "sendHeaders": True, "headerParameters": {"parameters": [{"name": "X-Key", "value": "abc"}]},
         "sendBody": True, "specifyBody": "json", "jsonBody": "={\"name\": \"{{ $json.name }}\"}", "authentication": "genericCredentialType"}
    r = convert({"nodes": [n("1", "T", "manualTrigger"), n("2", "Call", "httpRequest", p)], "connections": link({"T": [["Call"]]})})
    d = node_by_label(r, "Call")["data"]
    assert d["url"] == "https://api.example.com/items/{{id}}" and d["method"] == "POST" and json.loads(d["headers"]) == {"X-Key": "abc"} and d["body"] == '{"name": "{{name}}"}'
    assert any("Authentication" in m for m in messages(r, "Call"))


def test_code_nodes_keep_their_source_as_comments_and_still_run(client):
    js = "const items = $input.all();\nreturn items.map(i => ({json: {n: i.json.n + 1}}));"
    r = convert({"nodes": [n("1", "T", "manualTrigger"), n("2", "Js", "code", {"language": "javaScript", "jsCode": js})], "connections": link({"T": [["Js"]]})})
    code = node_by_label(r, "Js")["data"]["code"]
    assert "# const items = $input.all();" in code and code.strip().endswith("result = input")
    assert any("cannot be converted automatically" in m for m in messages(r, "Js"))
    wid = client.post("/api/workflows", json={k: r[k] for k in ("name", "description", "nodes", "edges")}).json()["id"]
    assert client.post(f"/api/workflows/{wid}/run", json={"input": {"n": 1}}).json()["status"] == "success"


def test_data_tools_map_across():
    flow = {"nodes": [
        n("1", "T", "manualTrigger"),
        n("2", "Sort", "sort", {"sortFieldsUi": {"sortField": [{"fieldName": "age", "order": "descending"}]}}),
        n("3", "Lim", "limit", {"maxItems": 3, "keep": "lastItems"}),
        n("4", "Dup", "removeDuplicates", {"compare": "selectedFields", "fieldsToCompare": "email, id"}),
        n("5", "Split", "splitOut", {"fieldToSplitOut": "data.rows"}),
        n("6", "Sum", "summarize", {"fieldsToSummarize": {"values": [{"aggregation": "sum", "field": "amount"}]}}),
        n("7", "Wait", "wait", {"amount": 5, "unit": "minutes"}),
        n("8", "Merge", "merge", {"mode": "combine"}),
    ], "connections": {}}
    r = convert(flow)
    assert node_by_label(r, "Sort")["data"]["order"] == "desc" and node_by_label(r, "Sort")["data"]["path"] == "age"
    assert node_by_label(r, "Lim")["data"]["from"] == "last" and node_by_label(r, "Lim")["data"]["count"] == "3"
    assert node_by_label(r, "Dup")["data"]["path"] == "email" and any("first comparison field" in m for m in messages(r, "Dup"))
    assert node_by_label(r, "Split")["data"]["path"] == "data.rows"
    assert (node_by_label(r, "Sum")["data"]["operation"], node_by_label(r, "Sum")["data"]["path"]) == ("sum", "amount")
    assert (node_by_label(r, "Wait")["data"]["seconds"], node_by_label(r, "Wait")["data"]["unit"]) == ("5", "minutes")
    assert node_by_label(r, "Merge")["data"]["mode"] == "combine"


def test_expressions_that_cannot_be_translated_are_left_visible_and_reported():
    flow = {"nodes": [n("1", "T", "manualTrigger"), n("2", "Tpl", "set", {"assignments": {"assignments": [
        {"name": "a", "value": "={{ $('Other Node').item.json.x }}", "type": "string"}, {"name": "b", "value": "={{ $json.items.map(i => i.id) }}", "type": "string"},
        {"name": "c", "value": "={{ $json['first name'] }}", "type": "string"}, {"name": "d", "value": "={{ $json.list[0].id }}", "type": "string"}]}})], "connections": link({"T": [["Tpl"]]})}
    r = convert(flow)
    out = node_by_label(r, "Tpl")["data"]["json"]
    assert "$('Other Node')" in out and "=>" in out  # kept as written, so the run fails loudly instead of using wrong data
    assert "{{data['first name']}}" in out  # a key with a space needs the expression form
    assert "{{list.0.id}}" in out
    assert len([m for m in messages(r, "Tpl") if "Rewrite it by hand" in m]) == 2


def test_graph_problems_are_reported():
    two_triggers = convert({"nodes": [n("1", "A", "manualTrigger"), n("2", "B", "scheduleTrigger", {"rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}})], "connections": {}})
    assert types(two_triggers) == {"A": "trigger", "B": "note"} and any("one trigger" in m for m in messages(two_triggers, "B"))
    no_trigger = convert({"nodes": [n("1", "Do", "noOp", pos=(300, 50))], "connections": {}})
    assert no_trigger["nodes"][0]["type"] == "trigger" and no_trigger["edges"] == [{"id": "start-1", "source": "start", "target": "1"}]
    cyc = convert({"nodes": [n("1", "T", "manualTrigger"), n("2", "A", "noOp"), n("3", "B", "noOp")], "connections": link({"T": [["A"]], "A": [["B"]], "B": [["A"]]})})
    assert any("loop back" in m for m in messages(cyc, "(workflow)"))
    fan = convert({"nodes": [n("1", "T", "manualTrigger"), n("2", "A", "noOp"), n("3", "B", "noOp"), n("4", "Join", "noOp")], "connections": link({"T": [["A", "B"]], "A": [["Join"]], "B": [["Join"]]})})
    assert any("Merge node" in m for m in messages(fan, "Join"))


def test_loops_and_sub_workflows_are_flagged():
    r = convert({"nodes": [n("1", "T", "manualTrigger"), n("2", "Loop", "splitInBatches"), n("3", "Sub", "executeWorkflow")], "connections": link({"T": [["Loop"]], "Loop": [["Sub"]]})})
    assert types(r)["Loop"] == "note" and types(r)["Sub"] == "note" and messages(r, "Loop") and messages(r, "Sub")


def test_extra_outputs_of_ordinary_nodes_are_dropped_with_a_warning():
    flow = {"nodes": [n("1", "T", "manualTrigger"), n("2", "Set", "set", {}), n("3", "X", "noOp")], "connections": {"T": {"main": [[{"node": "Set", "type": "main", "index": 0}]]}, "Set": {"main": [[], [{"node": "X", "type": "main", "index": 0}]]}}}
    r = convert(flow)
    assert any("Output 2" in m for m in messages(r, "Set")) and not any(e["target"] == "3" for e in r["edges"])


def test_more_service_nodes_map():
    flow = {"nodes": [n("1", "T", "manualTrigger"),
                      n("2", "Tg", "telegram", {"chatId": "123", "text": "=Hi {{ $json.name }}"}),
                      n("3", "Gh", "github", {"resource": "issue", "operation": "create", "owner": {"value": "me"}, "repository": {"value": "repo"}, "title": "T", "body": "B"}),
                      n("4", "Mail", "emailSend", {"toEmail": "a@b.co", "subject": "S", "text": "Body"}),
                      n("5", "Feed", "rssFeedRead", {"url": "https://example.com/feed"}),
                      n("6", "Hash", "crypto", {"action": "hash", "type": "SHA256", "value": "={{ $json.email }}"}),
                      n("7", "Dt", "dateTime", {"action": "formatDate", "date": "={{ $json.when }}", "format": "yyyy-MM-dd"}),
                      n("8", "Respond", "respondToWebhook", {"respondWith": "json", "responseBody": "={{ $json }}", "options": {"responseCode": 201}}),
                      n("9", "Note", "stickyNote", {"content": "Remember"}),
                      n("10", "Rss", "rssFeedReadTrigger", {"feedUrl": "https://example.com/new"})],
            "connections": {}}
    r = convert(flow)
    assert node_by_label(r, "Tg")["data"]["text"] == "Hi {{name}}" and node_by_label(r, "Tg")["data"]["chat_id"] == "123"
    assert node_by_label(r, "Gh")["data"]["repo"] == "me/repo"
    assert node_by_label(r, "Mail")["data"]["to"] == "a@b.co" and node_by_label(r, "Feed")["data"]["url"] == "https://example.com/feed"
    assert node_by_label(r, "Hash")["data"]["operation"] == "sha256" and node_by_label(r, "Hash")["data"]["value"] == "{{email}}"
    assert node_by_label(r, "Dt")["data"]["format"] == "%Y-%m-%d"
    assert node_by_label(r, "Respond")["data"]["status"] == "201" and node_by_label(r, "Note")["data"]["text"] == "Remember"
    assert types(r)["Rss"] == "note"  # a second trigger


@pytest.mark.parametrize("bad", [None, [], {}, {"nodes": []}, {"nodes": "x", "connections": {}}, {"nodes": [{"type": "x"}], "connections": []}])
def test_bad_input_is_rejected_clearly(bad):
    with pytest.raises(ValueError):
        convert(bad)


def test_too_many_nodes_are_refused():
    with pytest.raises(ValueError, match="allows"):
        convert({"nodes": [n(str(i), f"N{i}", "noOp") for i in range(61)], "connections": {}})


def test_duplicate_names_and_odd_ids_are_made_unique():
    r = convert({"nodes": [n("same id!", "Twin", "noOp"), n("same id!", "Twin", "noOp"), n("x" * 80, "Long", "noOp")], "connections": {}})
    ids = [x["id"] for x in r["nodes"] if x["id"] != "start"]
    assert len(set(ids)) == 3 and all(len(i) <= 31 and " " not in i and "!" not in i for i in ids)


def test_endpoint_rejects_non_workflows_and_does_not_save_anything(client):
    assert client.post("/api/import/n8n", json={"hello": "world"}).status_code == 400
    before = len(client.get("/api/workflows").json())
    assert client.post("/api/import/n8n", json=ORDER_ALERTS).status_code == 200
    assert len(client.get("/api/workflows").json()) == before


def test_viewers_cannot_use_the_import_endpoint(client):
    client.post("/api/users", json={"username": "viewer-n8n", "password": "long-enough-pw", "role": "viewer"})
    v = TestClient(main.app)
    assert v.post("/api/auth/login", json={"username": "viewer-n8n", "password": "long-enough-pw"}).status_code == 200
    assert v.post("/api/import/n8n", json=ORDER_ALERTS).status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'viewer-n8n')}")
