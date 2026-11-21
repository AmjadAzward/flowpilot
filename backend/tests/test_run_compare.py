import pytest

from app import main
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _env(clean_state):
    with main.conn() as db: db.execute("DELETE FROM executions")


def flow(client, name="Compared flow"):
    nodes = [node("t", "trigger"), node("i", "input", json='{"id": 7}'), node("h", "http", url="https://api.example.com/items/{{id}}", method="GET"), node("o", "output")]
    return make_flow(client, nodes, [edge("t", "i"), edge("i", "h"), edge("h", "o")], name=name)


def run(client, fake_http, wid, status=200, body=None):
    fake_http.reply["response"] = FakeResponse(status, body if body is not None else {"ok": True})
    return client.post(f"/api/workflows/{wid}/run").json()


def compare(client, a, b):
    return client.get("/api/executions/compare", params={"a": a["id"], "b": b["id"]})


def test_identical_runs_have_no_differences(client, fake_http):
    wid = flow(client)
    res = compare(client, run(client, fake_http, wid), run(client, fake_http, wid)).json()
    assert res["changed"] == 0 and res["first_change"] is None and not res["output_changed"] and not res["status_changed"] and res["same_workflow"]


def test_a_different_response_is_found_at_the_step_that_received_it(client, fake_http):
    wid = flow(client)
    res = compare(client, run(client, fake_http, wid, body={"price": 10}), run(client, fake_http, wid, body={"price": 12})).json()
    assert res["first_change"] == "h" and res["output_changed"]
    changed = [s for s in res["steps"] if s["change"]]
    assert changed[0]["node_id"] == "h" and "10" in changed[0]["a"]["output"] and "12" in changed[0]["b"]["output"]
    assert [s["change"] for s in res["steps"] if s["node_id"] in ("t", "i")] == [None, None]  # steps before it behaved the same


def test_a_success_against_a_failure_names_the_step_that_broke(client, fake_http):
    wid = flow(client)
    res = compare(client, run(client, fake_http, wid), run(client, fake_http, wid, status=500, body={"error": "down"})).json()
    assert res["status_changed"] and res["first_change"] == "h"
    step = next(s for s in res["steps"] if s["node_id"] == "h")
    assert step["change"] == "status" and step["a"]["status"] == "success" and step["b"]["status"] == "error"
    assert [s["node_id"] for s in res["steps"] if s["change"] == "only_in_a"] == ["o"]  # the failed run never reached the output step


def test_comparing_a_run_with_itself_is_fine(client, fake_http):
    r = run(client, fake_http, flow(client))
    assert compare(client, r, r).json()["changed"] == 0


def test_runs_of_different_workflows_still_compare(client, fake_http):
    a, b = run(client, fake_http, flow(client, "One")), run(client, fake_http, flow(client, "Two"))
    assert compare(client, a, b).json()["same_workflow"] is False


def test_unknown_runs_are_not_found(client, fake_http):
    r = run(client, fake_http, flow(client))
    assert client.get("/api/executions/compare", params={"a": r["id"], "b": "nope"}).status_code == 404
    assert client.get("/api/executions/compare", params={"a": "nope", "b": r["id"]}).status_code == 404
    assert client.get("/api/executions/compare", params={"a": r["id"]}).status_code == 422


def test_long_outputs_are_shortened(client, fake_http):
    wid = flow(client)
    res = compare(client, run(client, fake_http, wid, body={"t": "x" * 5000}), run(client, fake_http, wid, body={"t": "y" * 5000})).json()
    step = next(s for s in res["steps"] if s["node_id"] == "h")
    assert len(step["a"]["output"]) < 1600 and step["a"]["output"].endswith("(shortened)")


def test_compare_needs_a_login(anon):
    assert anon.get("/api/executions/compare", params={"a": "x", "b": "y"}).status_code == 401
