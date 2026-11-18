import json

import pytest

from app import main
from conftest import edge, make_flow, node

AI = "http://127.0.0.1:11434/v1?model=llama3.2&key=agent-secret-key"


@pytest.fixture(autouse=True)
def _clean(clean_state, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")
    yield


class Model:
    """A scripted chat model. `turns` are assistant messages returned in order; other URLs are answered by `pages`."""

    def __init__(self, monkeypatch, turns, pages=None):
        self.turns, self.pages, self.requests, self.fetched = list(turns), pages or {}, [], []
        monkeypatch.setattr(main, "do_request", self.fake)
        monkeypatch.setattr(main, "safe_url", lambda u: u)

    def fake(self, method, url, headers, **kw):
        if url.endswith("/chat/completions"):
            self.requests.append({"headers": headers, **kw})
            return {"choices": [{"message": self.turns.pop(0)}]}
        self.fetched.append(url)
        return self.pages[url]


def call(name, args, id_="call_1"):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def say(text): return {"role": "assistant", "content": text}


def use(tool_calls): return {"role": "assistant", "content": None, "tool_calls": tool_calls}


def agent(client, **cfg):
    client.put("/api/credentials/LLM", json={"value": AI})
    data = {"credential": "LLM", "prompt": "Help with {{topic}}", "system": "Be brief", "tools": "", "workflows": "", "max_steps": "4", **cfg}
    nodes = [node("t", "trigger"), node("i", "input", json='{"topic": "taxes"}'), node("a", "agent", **data), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "a"), edge("a", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


def test_a_plain_answer_needs_no_tools(client, monkeypatch):
    m = Model(monkeypatch, [say("Paying on time is wise.")])
    res = agent(client)
    assert res["status"] == "success" and res["output"] == {"answer": "Paying on time is wise.", "steps": [], "model": "llama3.2"}
    sent = m.requests[0]["json"]
    assert sent["messages"] == [{"role": "system", "content": "Be brief"}, {"role": "user", "content": "Help with taxes"}] and "tools" not in sent
    assert m.requests[0]["headers"] == {"Authorization": "Bearer agent-secret-key"}
    assert "agent-secret-key" not in json.dumps(res)


def test_the_calculator_tool_is_called_and_its_result_returned_to_the_model(client, monkeypatch):
    m = Model(monkeypatch, [use([call("calculator", {"expression": "round(12.5 * 3, 2)"})]), say("It is 37.5.")])
    res = agent(client, tools="calculator")
    assert res["output"]["answer"] == "It is 37.5." and res["output"]["steps"] == [{"tool": "calculator", "arguments": {"expression": "round(12.5 * 3, 2)"}, "result": "37.5"}]
    first, second = m.requests[0]["json"], m.requests[1]["json"]
    assert [t["function"]["name"] for t in first["tools"]] == ["calculator"] and first["tool_choice"] == "auto"
    assert second["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "37.5"} and second["messages"][-2]["tool_calls"][0]["id"] == "call_1"


def test_several_tools_and_the_clock(client, monkeypatch):
    Model(monkeypatch, [use([call("current_datetime", {}, "a"), call("calculator", {"expression": "2 + 2"}, "b")]), say("done")])
    res = agent(client, tools="calculator,current_datetime")
    steps = res["output"]["steps"]
    assert [s["tool"] for s in steps] == ["current_datetime", "calculator"] and "T" in steps[0]["result"] and steps[1]["result"] == "4"


def test_fetch_url_returns_readable_text_only(client, monkeypatch):
    html = "<html><head><style>p{color:red}</style><script>alert(1)</script></head><body><h1>Title</h1><p>Hello   <b>world</b></p></body></html>"
    m = Model(monkeypatch, [use([call("fetch_url", {"url": "https://example.com/page"})]), say("ok")], pages={"https://example.com/page": html})
    res = agent(client, tools="fetch_url")
    assert res["output"]["steps"][0]["result"] == "Title Hello world" and m.fetched == ["https://example.com/page"]


def test_a_tool_that_is_not_enabled_cannot_be_used_even_if_the_model_asks(client, monkeypatch):
    m = Model(monkeypatch, [use([call("fetch_url", {"url": "https://example.com"})]), say("sorry")], pages={"https://example.com": "secret"})
    res = agent(client, tools="calculator")
    assert res["output"]["steps"][0]["result"].startswith("Error:") and m.fetched == []
    assert [t["function"]["name"] for t in m.requests[0]["json"]["tools"]] == ["calculator"]


def test_tool_errors_are_returned_to_the_model_not_thrown(client, monkeypatch):
    Model(monkeypatch, [use([call("calculator", {"expression": "1 / 0"}), call("nonsense", {})]), say("I could not compute that.")])
    res = agent(client, tools="calculator")
    assert res["status"] == "success" and all(s["result"].startswith("Error:") for s in res["output"]["steps"])
    assert "division by zero" in res["output"]["steps"][0]["result"]


def test_bad_arguments_from_the_model_are_tolerated(client, monkeypatch):
    broken = {"id": "x", "type": "function", "function": {"name": "calculator", "arguments": "{not json"}}
    Model(monkeypatch, [use([broken]), say("fine")])
    res = agent(client, tools="calculator")
    assert res["status"] == "success" and res["output"]["steps"][0]["result"].startswith("Error:")


def test_an_agent_can_run_a_chosen_workflow_and_only_that_one(client, monkeypatch):
    chosen = make_flow(client, [node("t", "trigger"), node("m", "template", template="Order {{id}} is shipped"), node("o", "output")], [edge("t", "m"), edge("m", "o")], name="Order status")
    other = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="Not allowed")
    m = Model(monkeypatch, [use([call("workflow_1", {"input": {"id": 42}}, "a"), call("workflow_2", {}, "b")]), say("Order 42 is shipped.")])
    res = agent(client, workflows=chosen)
    steps = res["output"]["steps"]
    assert steps[0]["tool"] == "workflow_1" and steps[0]["result"] == '"Order 42 is shipped"' and steps[1]["result"].startswith("Error:")
    spec = m.requests[0]["json"]["tools"]
    assert [t["function"]["name"] for t in spec] == ["workflow_1"] and "Order status" in spec[0]["function"]["description"] and other not in json.dumps(spec)


def test_a_workflow_tool_cannot_call_the_workflow_that_runs_the_agent(client, monkeypatch):
    client.put("/api/credentials/LLM", json={"value": AI})
    holder = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="Holder")
    data = {"credential": "LLM", "prompt": "go", "workflows": holder, "max_steps": "2", "tools": ""}
    nodes = [node("t", "trigger"), node("a", "agent", **data), node("o", "output")]
    client.put(f"/api/workflows/{holder}", json={"name": "Holder", "description": "", "nodes": nodes, "edges": [edge("t", "a"), edge("a", "o")]})
    Model(monkeypatch, [use([call("workflow_1", {})]), say("the loop was refused"), use([call("workflow_1", {})]), say("x")])
    res = client.post(f"/api/workflows/{holder}/run").json()
    assert res["status"] == "success" and "cannot call itself" in res["output"]["steps"][0]["result"]


def test_the_step_limit_forces_a_final_answer(client, monkeypatch):
    forever = use([call("calculator", {"expression": "1"})])
    m = Model(monkeypatch, [forever, forever, say("Best effort answer.")])
    res = agent(client, tools="calculator", max_steps="2")
    out = res["output"]
    assert out["answer"] == "Best effort answer." and len(out["steps"]) == 2 and "Stopped after 2 steps" in out["note"]
    assert "tools" not in m.requests[-1]["json"] and "final answer" in m.requests[-1]["json"]["messages"][-1]["content"]


def test_at_most_four_tool_calls_run_per_step(client, monkeypatch):
    Model(monkeypatch, [use([call("calculator", {"expression": str(i)}, f"c{i}") for i in range(9)]), say("ok")])
    assert len(agent(client, tools="calculator")["output"]["steps"]) == 4


def test_setup_mistakes_are_reported_clearly(client, monkeypatch):
    Model(monkeypatch, [])
    assert "Write the task" in agent(client, prompt="  ")["error"]
    assert "Unknown tool" in agent(client, tools="calculator,hack")["error"]
    assert "at most 5" in agent(client, workflows="a,b,c,d,e,f")["error"]
    assert "Maximum steps" in agent(client, max_steps="lots")["error"]
    assert agent(client, credential="MISSING")["status"] == "failed"


def test_a_confused_model_reply_fails_cleanly(client, monkeypatch):
    monkeypatch.setattr(main, "do_request", lambda *a, **k: {"unexpected": True})
    res = agent(client)
    assert res["status"] == "failed" and "unexpected reply" in res["error"]


def test_the_agent_stops_when_it_runs_too_long(client, monkeypatch):
    Model(monkeypatch, [use([call("calculator", {"expression": "1"})]), say("never reached")])
    monkeypatch.setattr(main, "AGENT_DEADLINE_SECONDS", -1)
    res = agent(client, tools="calculator")
    assert res["status"] == "failed" and "was stopped" in res["error"]


def test_a_workflow_the_user_cannot_see_cannot_be_given_to_an_agent(client):
    from fastapi.testclient import TestClient
    pid = client.post("/api/projects", json={"name": "Hidden"}).json()["id"]
    secret = client.post("/api/workflows", params={"project_id": pid}, json={"name": "Secret", "description": "", "nodes": [node("t", "trigger"), node("o", "output")], "edges": [edge("t", "o")]}).json()["id"]
    uid = client.post("/api/users", json={"username": "agent-ed", "password": "long-enough-pw", "role": "editor"}).json()["id"]
    ed = TestClient(main.app)
    assert ed.post("/api/auth/login", json={"username": "agent-ed", "password": "long-enough-pw"}).status_code == 200
    body = {"name": "Mine", "description": "", "nodes": [node("t", "trigger"), node("a", "agent", credential="LLM", prompt="x", workflows=secret), node("o", "output")], "edges": [edge("t", "a"), edge("a", "o")]}
    res = ed.post("/api/workflows", json=body)
    assert res.status_code == 403 and "AI Agent" in res.json()["detail"]
    client.delete(f"/api/users/{uid}")
    client.delete(f"/api/workflows/{secret}")
    client.delete(f"/api/projects/{pid}")


def test_pin_and_continue_on_error_work_like_other_nodes(client, monkeypatch):
    Model(monkeypatch, [])
    pinned = agent(client, pin='{"answer": "pinned"}')
    assert pinned["output"] == {"answer": "pinned"}
    cont = agent(client, credential="MISSING", on_error="continue")
    assert cont["status"] == "success" and "error" in cont["output"]
