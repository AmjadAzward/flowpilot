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


def data_in(payload):
    return node("i", "input", json=json.dumps(payload))


def code_run(client, code, payload=None):
    nodes = [node("t", "trigger"), data_in(payload if payload is not None else {"n": 4}), node("c", "code", code=code), node("o", "output")]
    return run(client, nodes, [edge("t", "i"), edge("i", "c"), edge("c", "o")])


# ---------------------------------------------------------------- Switch
def switch_flow(client, value):
    nodes = [node("t", "trigger"), data_in({"status": value}), node("s", "switch", path="status", case1="new", case2="paid", case3="", case4="7"),
             node("a", "template", template="A"), node("b", "template", template="B"), node("d", "template", template="D"), node("m", "template", template="M")]
    edges = [edge("t", "i"), edge("i", "s"), edge("s", "a", "1"), edge("s", "b", "2"), edge("s", "d", "default"), edge("s", "m", "4")]
    return run(client, nodes, edges)


@pytest.mark.parametrize("value,expected,skipped", [("new", "A", {"b", "d", "m"}), ("paid", "B", {"a", "d", "m"}), ("zzz", "D", {"a", "b", "m"}), (7, "M", {"a", "b", "d"})])
def test_switch_routes_to_the_matching_path(client, value, expected, skipped):
    res = switch_flow(client, value)
    assert res["status"] == "success" and res["output"] == expected
    ran = {s["node_id"] for s in res["steps"]}
    assert not (ran & skipped)


def test_switch_empty_case_never_matches(client):
    nodes = [node("t", "trigger"), data_in({"status": ""}), node("s", "switch", path="status", case1="", case2="", case3="", case4=""), node("d", "template", template="D")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "s"), edge("s", "d", "default")])
    assert res["output"] == "D"


# ---------------------------------------------------------------- Code
def test_code_transforms_data(client):
    res = code_run(client, "result = {'double': input['n'] * 2, 'sq': math.sqrt(input['n'])}")
    assert res["status"] == "success" and res["output"] == {"double": 8, "sq": 2.0}


def test_code_has_json_re_and_datetime(client):
    res = code_run(client, "m = re.findall(r'\\d+', 'a1b22')\nresult = [m, json.loads('[1]'), datetime.timedelta(days=1).days]")
    assert res["output"] == [["1", "22"], [1], 1]


def test_code_syntax_and_runtime_errors_are_reported(client):
    assert "Syntax error" in code_run(client, "result = (")["error"]
    assert "ZeroDivisionError" in code_run(client, "result = 1/0")["error"] or "division" in code_run(client, "result = 1/0")["error"]


@pytest.mark.parametrize("code", [
    "import os\nresult = 1",
    "from os import path",
    "result = open('flowpilot.db').read()",
    "result = __import__('os').listdir('.')",
    "result = ().__class__.__bases__",
    "result = '{0.__class__}'.format(1)",
    "result = getattr(1, 'real')",
    "result = eval('1')",
    "result = exec('x=1')",
    "f = lambda: 0\nresult = f.__globals__",
    "result = json.codecs",
])
def test_code_blocks_escapes(client, code):
    res = code_run(client, code)
    assert res["status"] == "failed", code


def test_code_times_out(client, monkeypatch):
    monkeypatch.setattr(main, "CODE_TIMEOUT", 1)
    res = code_run(client, "while True:\n    pass")
    assert res["status"] == "failed" and "longer than" in res["error"]


def test_code_cannot_see_credentials_or_env(client):
    client.put("/api/credentials/TOPSECRET", json={"value": "supersecretvalue1"})
    res = code_run(client, "result = [k for k in dir()]")
    assert res["status"] == "failed" or "supersecretvalue1" not in json.dumps(res)


def test_code_output_is_redacted_when_it_echoes_a_credential(client):
    client.put("/api/credentials/TOPSECRET", json={"value": "supersecretvalue1"})
    res = code_run(client, "result = 'supersecretvalue1'")
    assert "supersecretvalue1" not in json.dumps(res)


# ---------------------------------------------------------------- Stop and Fail
def test_fail_node_stops_with_message(client):
    nodes = [node("t", "trigger"), data_in({"name": "Ada"}), node("f", "fail", message="bad {{name}}"), node("o", "output")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "f"), edge("f", "o")])
    assert res["status"] == "failed" and res["error"] == "bad Ada"
    assert not any(s["node_id"] == "o" for s in res["steps"])


# ---------------------------------------------------------------- retries / continue
class Flaky:
    def __init__(self, fail_times):
        self.left, self.calls = fail_times, 0


def flaky_code_node(**extra):
    return node("h", "set", json='{"x": {{n}}}', **extra)


def test_retries_succeed_after_transient_failures(client, monkeypatch):
    f = Flaky(2)
    real = main.step_action

    def flaky(n, previous):
        if n["id"] == "h":
            f.calls += 1
            if f.left > 0:
                f.left -= 1
                raise ValueError("temporary")
        return real(n, previous)

    monkeypatch.setattr(main, "step_action", flaky)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    nodes = [node("t", "trigger"), data_in({"n": 1}), flaky_code_node(retries="3", retry_wait="0"), node("o", "output")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "h"), edge("h", "o")])
    assert res["status"] == "success" and f.calls == 3
    assert next(s for s in res["steps"] if s["node_id"] == "h")["attempts"] == 3


def test_retries_give_up_and_fail(client, monkeypatch):
    calls = []
    real = main.step_action

    def always(n, previous):
        if n["id"] == "h":
            calls.append(1)
            raise ValueError("down")
        return real(n, previous)

    monkeypatch.setattr(main, "step_action", always)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    nodes = [node("t", "trigger"), data_in({"n": 1}), flaky_code_node(retries="2"), node("o", "output")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "h"), edge("h", "o")])
    assert res["status"] == "failed" and res["error"] == "down" and len(calls) == 3


def test_retries_are_capped(client, monkeypatch):
    calls = []

    def always(n, previous):
        if n["id"] == "h":
            calls.append(1)
            raise ValueError("down")
        return previous, previous, None

    monkeypatch.setattr(main, "step_action", always)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    nodes = [node("t", "trigger"), data_in({"n": 1}), flaky_code_node(retries="999"), node("o", "output")]
    run(client, nodes, [edge("t", "i"), edge("i", "h"), edge("h", "o")])
    assert len(calls) == main.MAX_RETRIES + 1


def test_continue_on_error_passes_the_error_downstream(client):
    nodes = [node("t", "trigger"), data_in({"n": 1}), node("c", "code", code="result = 1/0", on_error="continue"), node("m", "template", template="got: {{error}}"), node("o", "output")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "c"), edge("c", "m"), edge("m", "o")])
    assert res["status"] == "success" and res["output"].startswith("got: ") and "ZeroDivision" in res["output"] or "division" in res["output"]
    step = next(s for s in res["steps"] if s["node_id"] == "c")
    assert step["status"] == "error" and step["continued"] is True


def test_default_is_still_stop_on_error(client):
    nodes = [node("t", "trigger"), data_in({"n": 1}), node("c", "code", code="result = 1/0"), node("o", "output")]
    assert run(client, nodes, [edge("t", "i"), edge("i", "c"), edge("c", "o")])["status"] == "failed"


# ---------------------------------------------------------------- error workflow
def test_error_workflow_runs_with_failure_details(client):
    handler = make_flow(client, [node("t", "trigger"), node("m", "template", template="ALERT {{workflow.name}}: {{error}} at {{failed_node}}"), node("o", "output")],
                        [edge("t", "m"), edge("m", "o")], name="Handler")
    failing = make_flow(client, [node("t", "trigger", error_workflow=handler), data_in({"name": "Ada"}), node("f", "fail", message="boom", label="Exploder")],
                        [edge("t", "i"), edge("i", "f")], name="Main")
    res = client.post(f"/api/workflows/{failing}/run").json()
    assert res["status"] == "failed"
    runs = client.get("/api/executions", params={"workflow_id": handler}).json()
    assert len(runs) == 1 and runs[0]["trigger"] == "error" and runs[0]["status"] == "success"
    assert runs[0]["output"] == "ALERT Main: boom at Exploder"


def test_error_workflow_not_run_on_success_or_for_itself(client):
    handler = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="H2")
    ok = make_flow(client, [node("t", "trigger", error_workflow=handler), node("o", "output")], [edge("t", "o")])
    client.post(f"/api/workflows/{ok}/run")
    assert client.get("/api/executions", params={"workflow_id": handler}).json() == []


def test_failing_error_workflow_does_not_loop_or_break_the_original(client):
    a = make_flow(client, [node("t", "trigger"), node("f", "fail", message="inner")], [edge("t", "f")], name="A")
    b = make_flow(client, [node("t", "trigger", error_workflow=a), node("f", "fail", message="outer")], [edge("t", "f")], name="B")
    res = client.post(f"/api/workflows/{b}/run").json()
    assert res["status"] == "failed" and res["error"] == "outer"
    assert len(client.get("/api/executions", params={"workflow_id": a}).json()) == 1
    assert len(client.get("/api/executions", params={"workflow_id": b}).json()) == 1


def test_new_node_types_are_accepted_by_the_api(client):
    for t in ("switch", "code", "fail"):
        make_flow(client, [node("t", "trigger"), node("x", t)], [edge("t", "x")])
