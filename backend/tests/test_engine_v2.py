import json

import pytest

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def run(client, nodes, edges):
    wid = make_flow(client, nodes, edges)
    res = client.post(f"/api/workflows/{wid}/run")
    assert res.status_code == 200
    return res.json()


def data_in(payload, **extra):
    return node("i", "input", json=json.dumps(payload), **extra)


def loop_flow(items, body_nodes, body_edges, loop_data=None, done=True):
    nodes = [node("t", "trigger"), data_in(items), node("l", "loop", **(loop_data or {}))] + body_nodes
    edges = [edge("t", "i"), edge("i", "l")] + body_edges
    if done:
        nodes.append(node("o", "output"))
        edges.append(edge("l", "o", "done"))
    return nodes, edges


# ---------------------------------------------------------------- loop
def test_loop_runs_body_per_item_and_collects_results(client):
    nodes, edges = loop_flow([{"n": 1}, {"n": 2}, {"n": 3}], [node("m", "template", template="item {{n}}")], [edge("l", "m", "each")])
    res = run(client, nodes, edges)
    assert res["status"] == "success" and res["output"] == ["item 1", "item 2", "item 3"]
    body_steps = [s for s in res["steps"] if s["node_id"] == "m"]
    assert [s["iteration"] for s in body_steps] == [[0], [1], [2]]
    assert next(s for s in res["steps"] if s["node_id"] == "l")["output"] == {"items": 3}


def test_loop_can_read_a_list_field(client):
    nodes = [node("t", "trigger"), data_in({"rows": [1, 2]}), node("l", "loop", path="rows"), node("m", "template", template="v{{}}"), node("o", "output")]
    edges = [edge("t", "i"), edge("i", "l"), edge("l", "m", "each"), edge("l", "o", "done")]
    assert run(client, nodes, edges)["output"] == ["v1", "v2"]


def test_loop_with_condition_inside_body(client):
    body = [node("c", "condition", path="n", op="gt", equals="5"), node("hi", "template", template="big"), node("lo", "template", template="small")]
    edges = [edge("l", "c", "each"), edge("c", "hi", "true"), edge("c", "lo", "false")]
    nodes, edges = loop_flow([{"n": 1}, {"n": 9}], body, edges)
    assert run(client, nodes, edges)["output"] == ["small", "big"]


def test_nested_loops(client):
    body = [node("l2", "loop"), node("m", "template", template="x{{}}")]
    edges = [edge("l", "l2", "each"), edge("l2", "m", "each")]
    nodes, edges = loop_flow([[1, 2], [3]], body, edges)
    assert run(client, nodes, edges)["output"] == [["x1", "x2"], ["x3"]]


def test_loop_without_each_branch_passes_items_through(client):
    nodes, edges = loop_flow([1, 2], [], [])
    assert run(client, nodes, edges)["output"] == [1, 2]


def test_loop_over_empty_list(client):
    nodes, edges = loop_flow([], [node("m", "template", template="x")], [edge("l", "m", "each")])
    res = run(client, nodes, edges)
    assert res["status"] == "success" and res["output"] == []


def test_loop_requires_a_list(client):
    nodes, edges = loop_flow({"a": 1}, [], [])
    res = run(client, nodes, edges)
    assert res["status"] == "failed" and "needs a list" in res["error"]


def test_loop_item_limit(client):
    nodes, edges = loop_flow(list(range(main.MAX_LOOP_ITEMS + 1)), [], [])
    res = run(client, nodes, edges)
    assert res["status"] == "failed" and "limited" in res["error"]


def test_loop_body_failure_stops_the_run_and_marks_steps(client):
    body = [node("x", "transform", path="a"), node("bad", "input", json="{oops")]
    nodes, edges = loop_flow([1, 2], body, [edge("l", "bad", "each")])
    res = run(client, nodes, edges)
    assert res["status"] == "failed" and res["steps"][-1]["status"] == "error"
    assert next(s for s in res["steps"] if s["node_id"] == "l")["status"] == "error"


def test_loop_outputs_need_handles(client):
    nodes, edges = loop_flow([1], [node("m", "note")], [edge("l", "m")])
    res = run(client, nodes, edges)
    assert res["status"] == "failed" and "'each' or 'done'" in res["error"]


def test_each_and_done_paths_cannot_join(client):
    nodes = [node("t", "trigger"), data_in([1]), node("l", "loop"), node("a", "note"), node("j", "merge")]
    edges = [edge("t", "i"), edge("i", "l"), edge("l", "a", "each"), edge("l", "j", "done"), edge("a", "j")]
    res = run(client, nodes, edges)
    assert res["status"] == "failed" and "cannot join" in res["error"]


def test_total_step_limit_protects_against_runaway_loops(client):
    chain = [node(f"n{k}", "note") for k in range(6)]
    edges = [edge("l", "n0", "each")] + [edge(f"n{k}", f"n{k + 1}") for k in range(5)]
    nodes, edges = loop_flow(list(range(main.MAX_LOOP_ITEMS)), chain, edges)
    res = run(client, nodes, edges)
    assert res["status"] == "failed" and "Maximum execution steps" in res["error"]


# ---------------------------------------------------------------- merge
def fan_merge(mode, left=None, right=None):
    nodes = [node("t", "trigger"), data_in({"seed": 1}), node("a", "set", json=json.dumps(left or {"x": 1})),
             node("b", "set", json=json.dumps(right or {"y": 2})), node("m", "merge", mode=mode), node("o", "output")]
    edges = [edge("t", "i"), edge("i", "a"), edge("i", "b"), edge("a", "m"), edge("b", "m"), edge("m", "o")]
    return nodes, edges


def test_merge_append_combine_first(client):
    assert run(client, *fan_merge("append"))["output"] == [{"x": 1}, {"y": 2}]
    assert run(client, *fan_merge("combine"))["output"] == {"x": 1, "y": 2}
    assert run(client, *fan_merge("first"))["output"] == {"x": 1}


def test_merge_combine_later_input_wins(client):
    assert run(client, *fan_merge("combine", {"k": 1}, {"k": 2}))["output"] == {"k": 2}


def test_merge_combine_needs_objects(client):
    res = run(client, *fan_merge("combine", left=[1]))
    assert res["status"] == "failed" and "object" in res["error"]


def test_merge_unknown_mode(client):
    assert run(client, *fan_merge("weird"))["status"] == "failed"


def test_merge_runs_once_even_when_one_branch_is_not_taken(client):
    nodes = [node("t", "trigger"), data_in({"n": 9}), node("c", "condition", path="n", op="gt", equals="5"),
             node("a", "template", template="yes"), node("b", "template", template="no"), node("m", "merge", mode="append"), node("o", "output")]
    edges = [edge("t", "i"), edge("i", "c"), edge("c", "a", "true"), edge("c", "b", "false"), edge("a", "m"), edge("b", "m"), edge("m", "o")]
    res = run(client, nodes, edges)
    assert res["status"] == "success" and res["output"] == ["yes"]
    assert [s["node_id"] for s in res["steps"]].count("m") == 1


def test_merge_ignores_edges_from_unreachable_nodes(client):
    nodes = [node("t", "trigger"), data_in({"v": 1}), node("m", "merge"), node("ghost", "note"), node("o", "output")]
    edges = [edge("t", "i"), edge("i", "m"), edge("ghost", "m"), edge("m", "o")]
    res = run(client, nodes, edges)
    assert res["status"] == "success" and res["output"] == [{"v": 1}]


def test_cycle_through_a_merge_is_rejected(client):
    nodes = [node("t", "trigger"), node("m", "merge"), node("x", "note")]
    res = run(client, nodes, [edge("t", "m"), edge("m", "x"), edge("x", "m")])
    assert res["status"] == "failed" and "Cycles" in res["error"]


def test_only_merge_may_have_several_inputs(client):
    nodes = [node("t", "trigger"), node("a", "note"), node("b", "note"), node("j", "note")]
    res = run(client, nodes, [edge("t", "a"), edge("t", "b"), edge("a", "j"), edge("b", "j")])
    assert res["status"] == "failed" and "Merge" in res["error"]


# ---------------------------------------------------------------- pinned data and step details
def test_pinned_node_uses_pinned_data_instead_of_running(client):
    nodes = [node("t", "trigger"), node("h", "http", url="https://127.0.0.1/never-called", pin='{"cached": true}'), node("o", "output")]
    res = run(client, nodes, [edge("t", "h"), edge("h", "o")])
    assert res["status"] == "success" and res["output"] == {"cached": True}
    assert next(s for s in res["steps"] if s["node_id"] == "h")["pinned"] is True


def test_invalid_pin_fails_the_step(client):
    nodes = [node("t", "trigger"), node("x", "transform", path="a", pin="{nope")]
    assert run(client, nodes, [edge("t", "x")])["status"] == "failed"


def test_pin_is_ignored_on_logic_and_trigger_nodes(client):
    nodes = [node("t", "trigger", pin='{"fake":1}'), data_in({"n": 9}), node("c", "condition", path="n", op="gt", equals="5", pin='"ignored"'),
             node("a", "template", template="yes"), node("o", "output")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "c"), edge("c", "a", "true"), edge("a", "o")])
    assert res["output"] == "yes"
    assert not any(s.get("pinned") for s in res["steps"])


def test_steps_record_their_input(client):
    nodes = [node("t", "trigger"), data_in({"a": 1}), node("x", "transform", path="a")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "x")])
    assert next(s for s in res["steps"] if s["node_id"] == "x")["input"] == {"a": 1}


def test_huge_step_input_is_truncated(client):
    nodes = [node("t", "trigger"), data_in(list(range(8000))), node("x", "note")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "x")])
    stored = next(s for s in res["steps"] if s["node_id"] == "x")["input"]
    assert stored["truncated"] is True and len(stored["preview"]) <= 2000


# ---------------------------------------------------------------- sub-workflows
def make_child(client, template="child got {{name}}"):
    return make_flow(client, [node("t", "trigger"), node("m", "template", template=template), node("o", "output")], [edge("t", "m"), edge("m", "o")], name="child")


def parent_of(client, child_id):
    return run(client, [node("t", "trigger"), data_in({"name": "Ada"}), node("s", "subflow", workflow_id=child_id), node("o", "output")],
               [edge("t", "i"), edge("i", "s"), edge("s", "o")])


def test_subflow_runs_another_workflow_with_the_current_data(client):
    res = parent_of(client, make_child(client))
    assert res["status"] == "success" and res["output"] == "child got Ada"


def test_subflow_failure_is_reported_in_the_parent(client):
    bad = make_flow(client, [node("t", "trigger"), node("i", "input", json="{oops")], [edge("t", "i")], name="broken child")
    res = parent_of(client, bad)
    assert res["status"] == "failed" and "broken child" in res["error"]


def test_subflow_needs_a_valid_target(client):
    assert "Choose a workflow" in parent_of(client, "")["error"]
    assert "not found" in parent_of(client, "no-such-id")["error"]


def test_workflow_cannot_call_itself(client):
    wid = make_flow(client, [node("t", "trigger")], [], name="loop")
    body = {"name": "loop", "description": "", "nodes": [node("t", "trigger"), node("s", "subflow", workflow_id=wid)], "edges": [edge("t", "s")]}
    client.put(f"/api/workflows/{wid}", json=body)
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "cannot call itself" in res["error"]


def test_mutual_recursion_is_blocked(client):
    a = make_flow(client, [node("t", "trigger")], [], name="A")
    b = make_flow(client, [node("t", "trigger"), node("s", "subflow", workflow_id=a)], [edge("t", "s")], name="B")
    client.put(f"/api/workflows/{a}", json={"name": "A", "description": "", "nodes": [node("t", "trigger"), node("s", "subflow", workflow_id=b)], "edges": [edge("t", "s")]})
    res = client.post(f"/api/workflows/{a}/run").json()
    assert res["status"] == "failed" and "cannot call itself" in res["error"]


def test_subflow_nesting_depth_is_limited(client):
    ids = [make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="leaf")]
    for k in range(6):
        ids.append(make_flow(client, [node("t", "trigger"), node("s", "subflow", workflow_id=ids[-1])], [edge("t", "s")], name=f"level{k}"))
    assert client.post(f"/api/workflows/{ids[2]}/run").json()["status"] == "success"
    deep = client.post(f"/api/workflows/{ids[-1]}/run").json()
    assert deep["status"] == "failed" and "nested too deeply" in deep["error"]


# ---------------------------------------------------------------- set / filter / csv
def test_set_fields_builds_json_and_escapes_values(client):
    nodes = [node("t", "trigger"), data_in({"name": 'Ada "Q"\nLine2', "n": 3}), node("s", "set", json='{"who":"{{name}}","count":{{n}},"all":{{}}}')]
    res = run(client, nodes, [edge("t", "i"), edge("i", "s")])
    assert res["status"] == "success"
    assert res["output"]["who"] == 'Ada "Q"\nLine2' and res["output"]["count"] == 3 and res["output"]["all"]["n"] == 3


def test_set_fields_rejects_invalid_json(client):
    nodes = [node("t", "trigger"), node("s", "set", json="{not json}")]
    assert run(client, nodes, [edge("t", "s")])["status"] == "failed"


def test_filter_list(client):
    items = [{"n": 1}, {"n": 5}, {"n": 9}]
    nodes = [node("t", "trigger"), data_in(items), node("f", "filter", path="n", op="gt", equals="4")]
    assert run(client, nodes, [edge("t", "i"), edge("i", "f")])["output"] == [{"n": 5}, {"n": 9}]
    nodes = [node("t", "trigger"), data_in([1, 5, 9]), node("f", "filter", op="gt", equals="4")]
    assert run(client, nodes, [edge("t", "i"), edge("i", "f")])["output"] == [5, 9]


def test_filter_needs_a_list(client):
    nodes = [node("t", "trigger"), data_in({"a": 1}), node("f", "filter")]
    assert run(client, nodes, [edge("t", "i"), edge("i", "f")])["status"] == "failed"


CSV_TEXT = "name,age\nAda,36\nGrace,45"


def test_parse_csv(client):
    def go(**cfg):
        return run(client, [node("t", "trigger"), data_in(CSV_TEXT), node("c", "csv", **cfg)], [edge("t", "i"), edge("i", "c")])
    assert go()["output"] == [{"name": "Ada", "age": "36"}, {"name": "Grace", "age": "45"}]
    assert go(header="false")["output"] == [["name", "age"], ["Ada", "36"], ["Grace", "45"]]


def test_parse_csv_from_a_field_and_error_cases(client):
    nodes = [node("t", "trigger"), data_in({"body": CSV_TEXT}), node("c", "csv", path="body")]
    assert len(run(client, nodes, [edge("t", "i"), edge("i", "c")])["output"]) == 2
    nodes = [node("t", "trigger"), data_in({"not": "text"}), node("c", "csv")]
    assert run(client, nodes, [edge("t", "i"), edge("i", "c")])["status"] == "failed"
    big = "a\n" + "1\n" * 10_001
    nodes = [node("t", "trigger"), data_in(big), node("c", "csv")]
    assert "10000 rows" in run(client, nodes, [edge("t", "i"), edge("i", "c")])["error"]
    nodes = [node("t", "trigger"), data_in(""), node("c", "csv")]
    assert run(client, nodes, [edge("t", "i"), edge("i", "c")])["output"] == []


def test_list_pipeline_csv_filter_loop(client):
    text = "name,score\nAda,90\nBob,40\nCy,75"
    nodes = [node("t", "trigger"), data_in(text), node("c", "csv"), node("f", "filter", path="score", op="gt", equals="50"), node("l", "loop"),
             node("m", "template", template="{{name}}:{{score}}"), node("o", "output")]
    edges = [edge("t", "i"), edge("i", "c"), edge("c", "f"), edge("f", "l"), edge("l", "m", "each"), edge("l", "o", "done")]
    assert run(client, nodes, edges)["output"] == ["Ada:90", "Cy:75"]
