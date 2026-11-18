import pytest
from conftest import edge, make_flow, node


def run(client, nodes, edges):
    wid = make_flow(client, nodes, edges)
    res = client.post(f"/api/workflows/{wid}/run")
    assert res.status_code == 200
    return res.json()


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_sample_workflow_seeded_and_runs(client):
    res = client.post("/api/workflows/demo-workflow/run").json()
    assert res["status"] == "success"
    assert res["output"] == "In Transit"
    assert all("duration_ms" in s for s in res["steps"])


def test_crud_roundtrip(client):
    wid = make_flow(client, [node("t", "trigger")], [])
    assert client.get(f"/api/workflows/{wid}").json()["name"] == "t"
    body = {"name": "renamed", "description": "d", "nodes": [node("t", "trigger")], "edges": []}
    assert client.put(f"/api/workflows/{wid}", json=body).json()["saved"] is True
    assert any(w["name"] == "renamed" for w in client.get("/api/workflows").json())
    assert client.delete(f"/api/workflows/{wid}").status_code == 204
    assert client.get(f"/api/workflows/{wid}").status_code == 404


def test_duplicate(client):
    res = client.post("/api/workflows/demo-workflow/duplicate")
    assert res.status_code == 201
    copy = client.get(f"/api/workflows/{res.json()['id']}").json()
    assert copy["name"].endswith("(copy)")
    assert len(copy["nodes"]) == 4


def test_duplicate_missing_returns_404(client):
    assert client.post("/api/workflows/nope/duplicate").status_code == 404


@pytest.mark.parametrize("nodes,edges", [
    ([node("a", "bogus")], []),
    ([node("a", "trigger"), node("a", "output")], []),
    ([node("a", "trigger")], [edge("a", "missing")]),
])
def test_validation_rejects_bad_workflows(client, nodes, edges):
    res = client.post("/api/workflows", json={"name": "x", "nodes": nodes, "edges": edges})
    assert res.status_code == 400


def test_too_many_nodes_rejected(client):
    nodes = [node(f"n{i}", "note") for i in range(61)]
    assert client.post("/api/workflows", json={"name": "x", "nodes": nodes, "edges": []}).status_code == 400


def test_empty_name_rejected(client):
    assert client.post("/api/workflows", json={"name": "", "nodes": [], "edges": []}).status_code == 422


def test_extract_nested_path_and_list_index(client):
    res = run(client,
        [node("t", "trigger"), node("i", "input", json='{"a":{"b":[10,20,30]}}'), node("x", "transform", path="a.b.1"), node("o", "output")],
        [edge("t", "i"), edge("i", "x"), edge("x", "o")])
    assert res["output"] == 20


def test_missing_path_gives_null(client):
    res = run(client,
        [node("t", "trigger"), node("i", "input", json='{"a":1}'), node("x", "transform", path="zzz"), node("o", "output")],
        [edge("t", "i"), edge("i", "x"), edge("x", "o")])
    assert res["status"] == "success" and res["output"] is None


def test_invalid_json_input_fails_with_step_error(client):
    res = run(client, [node("t", "trigger"), node("i", "input", json="{oops")], [edge("t", "i")])
    assert res["status"] == "failed"
    assert res["steps"][-1]["status"] == "error"
    assert "Invalid JSON" in res["error"]


def test_template_node(client):
    res = run(client,
        [node("t", "trigger"), node("i", "input", json='{"name":"Ada","n":3}'),
         node("m", "template", template="Hi {{name}}, n={{n}}, x={{missing}}."), node("o", "output")],
        [edge("t", "i"), edge("i", "m"), edge("m", "o")])
    assert res["output"] == "Hi Ada, n=3, x=."


def test_delay_node_passes_data_through_and_validates_input(client):
    res = run(client,
        [node("t", "trigger"), node("i", "input", json='{"k":1}'), node("d", "delay", seconds="0.01"), node("o", "output")],
        [edge("t", "i"), edge("i", "d"), edge("d", "o")])
    assert res["output"] == {"k": 1}
    bad = run(client, [node("t", "trigger"), node("d", "delay", seconds="not-a-number")], [edge("t", "d")])
    assert bad["status"] == "failed"


def test_delay_is_capped(client):
    res = run(client, [node("t", "trigger"), node("d", "delay", seconds="9999")], [edge("t", "d")])
    assert res["steps"][-1]["output"]["waited_seconds"] == 5.0


def branch_flow(op, expected, payload):
    return (
        [node("t", "trigger"), node("i", "input", json=payload), node("c", "condition", path="v", op=op, equals=expected),
         node("yes", "template", template="YES"), node("no", "template", template="NO")],
        [edge("t", "i"), edge("i", "c"), edge("c", "yes", "true"), edge("c", "no", "false")],
    )


@pytest.mark.parametrize("op,expected,payload,branch", [
    ("equals", "Active", '{"v":"active"}', "yes"),
    ("equals", "x", '{"v":"active"}', "no"),
    ("not_equals", "x", '{"v":"active"}', "yes"),
    ("contains", "cti", '{"v":"active"}', "yes"),
    ("gt", "5", '{"v":9}', "yes"),
    ("gt", "5", '{"v":"abc"}', "no"),
    ("lt", "5", '{"v":9}', "no"),
    ("exists", "", '{"v":0}', "yes"),
    ("exists", "", '{"w":0}', "no"),
    ("not_exists", "", '{"w":0}', "yes"),
])
def test_condition_routes_only_matching_branch(client, op, expected, payload, branch):
    res = run(client, *branch_flow(op, expected, payload))
    assert res["status"] == "success"
    ran = {s["node_id"] for s in res["steps"]}
    other = "no" if branch == "yes" else "yes"
    assert branch in ran and other not in ran


def test_condition_passes_original_value_downstream(client):
    nodes = [node("t", "trigger"), node("i", "input", json='{"v":1}'),
             node("c", "condition", path="v", op="equals", equals="1"), node("o", "output")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "c"), edge("c", "o", "true")])
    assert res["output"] == {"v": 1}


def test_unknown_operator_fails(client):
    nodes, edges = branch_flow("weird", "x", '{"v":1}')
    assert run(client, nodes, edges)["status"] == "failed"


def test_requires_exactly_one_trigger(client):
    assert run(client, [node("i", "input", json="{}")], [])["status"] == "failed"
    assert run(client, [node("a", "trigger"), node("b", "trigger")], [])["status"] == "failed"


def test_fan_in_and_cycles_rejected(client):
    fan = run(client, [node("t", "trigger"), node("a", "note"), node("b", "note"), node("j", "note")],
              [edge("t", "a"), edge("t", "b"), edge("a", "j"), edge("b", "j")])
    assert fan["status"] == "failed"
    cyc = run(client, [node("t", "trigger"), node("a", "note"), node("b", "note")],
              [edge("t", "a"), edge("a", "b"), edge("b", "a")])
    assert cyc["status"] == "failed"


def test_fan_out_runs_both_children(client):
    res = run(client, [node("t", "trigger"), node("a", "note"), node("b", "note")], [edge("t", "a"), edge("t", "b")])
    assert res["status"] == "success" and len(res["steps"]) == 3


@pytest.mark.parametrize("url", [
    "http://example.com", "https://localhost/x", "https://127.0.0.1/x", "https://169.254.169.254/latest",
    "https://user:pw@example.com/", "https://example.com:8443/", "https://foo.internal/", "https://10.0.0.1/",
])
def test_http_node_blocks_unsafe_urls(client, url):
    res = run(client, [node("t", "trigger"), node("h", "http", url=url)], [edge("t", "h")])
    assert res["status"] == "failed"


def test_http_node_rejects_unsupported_method(client):
    res = run(client, [node("t", "trigger"), node("h", "http", url="https://example.com", method="TRACE")], [edge("t", "h")])
    assert res["status"] == "failed" and "Unsupported HTTP method" in res["error"]


def test_executions_listed_filtered_and_cleared(client):
    wid = make_flow(client, [node("t", "trigger")], [])
    client.post(f"/api/workflows/{wid}/run")
    client.post(f"/api/workflows/{wid}/run")
    assert len(client.get("/api/executions", params={"workflow_id": wid}).json()) == 2
    assert client.delete("/api/executions", params={"workflow_id": wid}).status_code == 204
    assert client.get("/api/executions", params={"workflow_id": wid}).json() == []


def test_deleting_workflow_removes_its_executions(client):
    wid = make_flow(client, [node("t", "trigger")], [])
    client.post(f"/api/workflows/{wid}/run")
    client.delete(f"/api/workflows/{wid}")
    assert client.get("/api/executions", params={"workflow_id": wid}).json() == []
