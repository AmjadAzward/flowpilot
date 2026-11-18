import json
from datetime import datetime, timedelta, timezone

import pytest

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM waits")


def flow(client, wait_data, after=None):
    after = after or [node("m", "template", template="Hello {{name}} after waiting"), node("o", "output")]
    nodes = [node("t", "trigger"), node("i", "input", json='{"name": "Ada"}'), node("w", "wait", **wait_data)] + after
    edges = [edge("t", "i"), edge("i", "w"), edge("w", "m"), edge("m", "o")]
    return make_flow(client, nodes, edges)


def run(client, wid):
    return client.post(f"/api/workflows/{wid}/run").json()


def later(**kw):
    return datetime.now(timezone.utc) + timedelta(**kw)


def test_long_wait_suspends_the_run_and_stores_it(client):
    res = run(client, flow(client, {"seconds": "2", "unit": "hours"}))
    assert res["status"] == "waiting" and res["error"] is None
    steps = {s["node_id"]: s for s in res["steps"]}
    assert steps["w"]["status"] == "waiting" and "m" not in steps
    listed = next(e for e in client.get("/api/executions").json() if e["id"] == res["id"])
    assert listed["status"] == "waiting" and listed["resume_at"]
    resume_at = datetime.fromisoformat(listed["resume_at"])
    assert timedelta(minutes=119) < resume_at - datetime.now(timezone.utc) < timedelta(minutes=121)


def test_nothing_resumes_early(client):
    run(client, flow(client, {"seconds": "2", "unit": "hours"}))
    assert main.resume_due_waits() == 0


def test_due_wait_continues_where_it_stopped(client):
    res = run(client, flow(client, {"seconds": "5", "unit": "minutes"}))
    assert main.resume_due_waits(later(minutes=6)) == 1
    done = next(e for e in client.get("/api/executions").json() if e["id"] == res["id"])
    assert done["status"] == "success" and done["output"] == "Hello Ada after waiting" and done["resume_at"] is None
    statuses = {s["node_id"]: s["status"] for s in done["steps"]}
    assert statuses["w"] == "success" and statuses["m"] == "success"
    assert main.resume_due_waits(later(minutes=7)) == 0  # never twice


def test_resumed_failure_is_recorded_and_runs_the_error_workflow(client):
    handler = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="H")
    nodes = [node("t", "trigger", error_workflow=handler), node("i", "input", json="{}"), node("w", "wait", seconds="2", unit="minutes"), node("f", "fail", message="after wait")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "w"), edge("w", "f")])
    res = run(client, wid)
    assert res["status"] == "waiting"
    assert client.get("/api/executions", params={"workflow_id": handler}).json() == []  # waiting is not a failure
    main.resume_due_waits(later(minutes=3))
    done = next(e for e in client.get("/api/executions").json() if e["id"] == res["id"])
    assert done["status"] == "failed" and done["error"] == "after wait"
    assert len(client.get("/api/executions", params={"workflow_id": handler}).json()) == 1


def test_a_second_long_wait_in_the_continuation_waits_again(client):
    nodes = [node("t", "trigger"), node("w1", "wait", seconds="2", unit="minutes"), node("w2", "wait", seconds="1", unit="hours"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "w1"), edge("w1", "w2"), edge("w2", "o")])
    res = run(client, wid)
    assert main.resume_due_waits(later(minutes=3)) == 1
    mid = next(e for e in client.get("/api/executions").json() if e["id"] == res["id"])
    assert mid["status"] == "waiting" and mid["resume_at"]
    assert main.resume_due_waits(later(hours=2)) == 1
    assert next(e for e in client.get("/api/executions").json() if e["id"] == res["id"])["status"] == "success"


def test_cancel_a_waiting_run(client):
    res = run(client, flow(client, {"seconds": "1", "unit": "days"}))
    assert client.post(f"/api/executions/{res['id']}/cancel").status_code == 200
    assert main.resume_due_waits(later(days=2)) == 0
    assert next(e for e in client.get("/api/executions").json() if e["id"] == res["id"])["status"] == "failed"
    assert client.post(f"/api/executions/{res['id']}/cancel").status_code == 400
    assert client.post("/api/executions/nope/cancel").status_code == 404


def test_limits_and_invalid_values(client):
    assert run(client, flow(client, {"seconds": "8", "unit": "days"}))["status"] == "failed"
    assert run(client, flow(client, {"seconds": "abc"}))["status"] == "failed"
    assert run(client, flow(client, {"seconds": "1", "unit": "years"}))["status"] == "failed"


def test_long_wait_is_refused_inside_a_loop_and_a_sub_workflow(client):
    nodes = [node("t", "trigger"), node("i", "input", json="[1,2]"), node("l", "loop"), node("w", "wait", seconds="5", unit="minutes"), node("o", "output")]
    res = run(client, make_flow(client, nodes, [edge("t", "i"), edge("i", "l"), edge("l", "w", "each"), edge("l", "o", "done")]))
    assert res["status"] == "failed" and "loop" in res["error"]
    child = make_flow(client, [node("t", "trigger"), node("w", "wait", seconds="5", unit="minutes")], [edge("t", "w")], name="child")
    res = run(client, make_flow(client, [node("t", "trigger"), node("s", "subflow", workflow_id=child)], [edge("t", "s")]))
    assert res["status"] == "failed"


def test_pending_wait_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_PENDING_WAITS", 1)
    wid = flow(client, {"seconds": "1", "unit": "hours"})
    assert run(client, wid)["status"] == "waiting"
    res = run(client, wid)
    assert res["status"] == "failed" and "Too many" in res["error"]


def test_restart_recovery_closes_interrupted_resumes(client):
    res = run(client, flow(client, {"seconds": "2", "unit": "minutes"}))
    with main.conn() as db:
        db.execute("UPDATE waits SET status='running'")
    main.recover_waits()
    done = next(e for e in client.get("/api/executions").json() if e["id"] == res["id"])
    assert done["status"] == "failed" and "Interrupted" in done["error"]


def test_deleting_the_workflow_or_history_removes_waits(client):
    wid = flow(client, {"seconds": "1", "unit": "hours"})
    run(client, wid)
    client.delete(f"/api/workflows/{wid}")
    with main.conn() as db:
        assert db.execute("SELECT COUNT(*) c FROM waits").fetchone()["c"] == 0
    wid2 = flow(client, {"seconds": "1", "unit": "hours"})
    run(client, wid2)
    client.delete("/api/executions")
    with main.conn() as db:
        assert db.execute("SELECT COUNT(*) c FROM waits").fetchone()["c"] == 0


def test_webhook_with_a_long_wait_answers_immediately(client, anon):
    nodes = [node("w0", "webhook"), node("w", "wait", seconds="1", unit="hours"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("w0", "w"), edge("w", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    res = anon.post(f"/api/hooks/{token}", json={"a": 1})
    assert res.status_code == 200 and res.json()["status"] == "waiting"


def test_waiting_data_is_redacted_when_stored(client):
    client.put("/api/credentials/KEY", json={"value": "topsecretvalue99"})
    nodes = [node("t", "trigger"), node("i", "input", json='{"k": "topsecretvalue99"}'), node("w", "wait", seconds="1", unit="hours"), node("o", "output")]
    run(client, make_flow(client, nodes, [edge("t", "i"), edge("i", "w"), edge("w", "o")]))
    with main.conn() as db:
        assert "topsecretvalue99" not in db.execute("SELECT value FROM waits").fetchone()["value"]
