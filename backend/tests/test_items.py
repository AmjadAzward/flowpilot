import json

import pytest

from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


LIST = json.dumps([{"name": "ada", "n": 1}, {"name": "grace", "n": 2}, {"name": "linus", "n": 3}])


def run(client, middle, extra_edges=None):
    nodes = [node("t", "trigger"), node("i", "input", json=LIST), middle, node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", middle["id"]), edge(middle["id"], "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


def test_set_runs_once_per_item(client):
    res = run(client, node("s", "set", each_item="true", json='{"hello": "{{name}}", "double": {{ n * 2 }}}'))
    assert res["status"] == "success"
    assert res["output"] == [{"hello": "ada", "double": 2}, {"hello": "grace", "double": 4}, {"hello": "linus", "double": 6}]


def test_without_the_option_the_whole_list_is_one_input(client):
    res = run(client, node("s", "transform", path="0.name"))
    assert res["output"] == "ada"


def test_each_item_transform_and_template(client):
    assert run(client, node("s", "transform", each_item="true", path="name"))["output"] == ["ada", "grace", "linus"]
    assert run(client, node("s", "template", each_item="true", template="Hi {{ name.upper() }}"))["output"] == ["Hi ADA", "Hi GRACE", "Hi LINUS"]


def test_each_item_code_node(client):
    res = run(client, node("c", "code", each_item="true", code="result = {'len': len(input['name'])}"))
    assert res["output"] == [{"len": 3}, {"len": 5}, {"len": 5}]


def test_each_item_http_makes_one_call_per_item(client, fake_http):
    res = run(client, node("h", "http", each_item="true", method="GET", url="https://api.example.com/users/{{name}}"))
    assert res["status"] == "success"
    urls = [c["url"] for c in fake_http.calls]
    assert urls[-3:] == ["https://api.example.com/users/ada", "https://api.example.com/users/grace", "https://api.example.com/users/linus"]


def test_each_item_needs_a_list(client):
    nodes = [node("t", "trigger"), node("i", "input", json='{"a": 1}'), node("s", "set", each_item="true", json="{}"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "s"), edge("s", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "needs a list" in res["error"]


def test_a_failing_item_names_its_position(client):
    res = run(client, node("s", "template", each_item="true", template="{{ 10 / (n - 2) }}"))
    assert res["status"] == "failed" and "Item 2 of 3" in res["error"]


def test_empty_list_gives_empty_list(client):
    nodes = [node("t", "trigger"), node("i", "input", json="[]"), node("s", "set", each_item="true", json="{}"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "s"), edge("s", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["output"] == []


def test_limit_on_items(client):
    big = json.dumps([{"n": i} for i in range(201)])
    nodes = [node("t", "trigger"), node("i", "input", json=big), node("s", "set", each_item="true", json="{}"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "s"), edge("s", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "limited to 200" in res["error"]


def test_option_is_ignored_on_nodes_that_do_not_support_it(client):
    nodes = [node("t", "trigger"), node("i", "input", json=LIST), node("f", "filter", each_item="true", path="n", op="gt", equals="1"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "f"), edge("f", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success"
