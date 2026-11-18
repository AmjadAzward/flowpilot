import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def approval_flow(client, **cfg):
    data = {"seconds": "1", "unit": "days", "on_timeout": "continue", **cfg}
    nodes = [node("t", "trigger"), node("i", "input", json='{"order": 5}'),
             node("m", "set", json='{"order": {{order}}, "message": "Approve: {{run.approve_url}} Reject: {{run.reject_url}}"}'),
             node("w", "callback", **data),
             node("c", "condition", path="decision", op="equals", equals="approve"),
             node("y", "template", template="Approved order {{data.order}} note={{body.note}}"), node("n", "template", template="Not approved ({{decision}})"),
             node("o1", "output"), node("o2", "output")]
    edges = [edge("t", "i"), edge("i", "m"), edge("m", "w"), edge("w", "c"), edge("c", "y", "true"), edge("c", "n", "false"), edge("y", "o1"), edge("n", "o2")]
    return make_flow(client, nodes, edges)


def start(client, **cfg):
    wid = approval_flow(client, **cfg)
    run = client.post(f"/api/workflows/{wid}/run").json()
    assert run["status"] == "waiting", run
    text = next(s for s in run["steps"] if s["node_id"] == "m")["output"]["message"]
    links = dict(re.findall(r"(Approve|Reject): (\S+)", text))
    path = lambda u: u.split(":5173", 1)[1]
    return wid, run, path(links["Approve"]), path(links["Reject"])


def get_run(client, run_id):
    return next(r for r in client.get("/api/executions").json() if r["id"] == run_id)


def test_the_run_pauses_and_exposes_links_but_not_the_raw_token_twice(client):
    wid, run, approve, reject = start(client)
    assert approve.startswith("/api/resume/") and approve.endswith("?decision=approve") and reject.endswith("?decision=reject")
    wait_step = next(s for s in run["steps"] if s["node_id"] == "w")
    assert wait_step["status"] == "waiting" and "approve_url" in wait_step["output"] and "expires_at" in wait_step["output"]
    row = get_run(client, run["id"])
    assert row["status"] == "waiting" and row["waiting_for"] == "response" and row["resume_at"]


def test_approving_through_the_link_continues_the_run(client, anon):
    wid, run, approve, reject = start(client)
    res = anon.get(approve)
    assert res.status_code == 200 and "Thank you" in res.text
    done = get_run(client, run["id"])
    assert done["status"] == "success" and done["output"] == "Approved order 5 note="
    assert done["waiting_for"] is None
    waited = next(s for s in done["steps"] if s["node_id"] == "w")
    assert waited["status"] == "success" and waited["output"]["decision"] == "approve"


def test_rejecting_takes_the_other_branch(client, anon):
    wid, run, approve, reject = start(client)
    assert anon.get(reject).status_code == 200
    assert get_run(client, run["id"])["output"] == "Not approved (reject)"


def test_a_link_works_only_once(client, anon):
    wid, run, approve, reject = start(client)
    assert anon.get(approve).status_code == 200
    again = anon.get(reject)  # the other link of the same run is dead too
    assert again.status_code == 409 and "already answered" in again.text
    assert get_run(client, run["id"])["output"] == "Approved order 5 note="


def test_post_can_carry_data_into_the_run(client, anon):
    wid, run, approve, reject = start(client)
    res = anon.post(approve, json={"note": "looks fine"})
    assert res.status_code == 200 and res.json() == {"status": "resumed", "decision": "approve"}
    assert get_run(client, run["id"])["output"] == "Approved order 5 note=looks fine"


def test_a_custom_decision_and_a_bad_one(client, anon):
    wid, run, approve, reject = start(client)
    base_path = approve.split("?")[0]
    assert anon.get(base_path + "?decision=Not%20Valid!").status_code == 400
    assert anon.get(base_path + "?decision=maybe").status_code == 200
    assert get_run(client, run["id"])["output"] == "Not approved (maybe)"


def test_forged_or_tampered_links_are_refused(client, anon):
    wid, run, approve, reject = start(client)
    token = approve.split("/api/resume/")[1].split("?")[0]
    exec_id, sig = token.rsplit(".", 1)
    for bad in (f"{exec_id}.{'0' * 32}", f"{exec_id}.", f"{exec_id}", "nothing.at.all", f"{exec_id[:-1]}x.{sig}", f"other-run.{sig}", "." + sig):
        assert anon.get(f"/api/resume/{bad}?decision=approve").status_code == 404, bad
    assert get_run(client, run["id"])["status"] == "waiting"


def test_the_signature_secret_is_stable_and_private(client):
    assert main.callback_token("abc") == main.callback_token("abc") and main.callback_exec_id(main.callback_token("abc")) == "abc"
    with main.conn() as db: secret = db.execute("SELECT value FROM settings WHERE key='callback_secret'").fetchone()["value"]
    assert len(secret) == 64
    assert secret not in json.dumps(client.get("/api/settings").json())


def test_the_signed_in_api_can_answer_too(client):
    wid, run, approve, reject = start(client)
    res = client.post(f"/api/executions/{run['id']}/respond", json={"decision": "approve"})
    assert res.status_code == 200 and get_run(client, run["id"])["output"] == "Approved order 5 note="
    assert client.post(f"/api/executions/{run['id']}/respond", json={"decision": "approve"}).status_code == 409
    assert client.post("/api/executions/nope/respond", json={"decision": "approve"}).status_code == 409
    wid2, run2, _, _ = start(client)
    assert client.post(f"/api/executions/{run2['id']}/respond", json={"decision": "bad decision!"}).status_code == 400


def test_a_cancelled_run_can_no_longer_be_answered(client, anon):
    wid, run, approve, reject = start(client)
    assert client.post(f"/api/executions/{run['id']}/cancel").status_code == 200
    assert anon.get(approve).status_code == 409
    assert get_run(client, run["id"])["status"] == "failed"


def expire(run_id):
    with main.conn() as db: db.execute("UPDATE waits SET resume_at=? WHERE execution_id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), run_id))
    return main.resume_due_waits()


def test_timeout_can_continue_with_a_timeout_decision(client, anon):
    wid, run, approve, reject = start(client, on_timeout="continue")
    assert expire(run["id"]) == 1
    done = get_run(client, run["id"])
    assert done["status"] == "success" and done["output"] == "Not approved (timeout)"
    assert anon.get(approve).status_code == 409  # too late


def test_timeout_can_fail_the_run(client):
    wid, run, approve, reject = start(client, on_timeout="fail")
    expire(run["id"])
    done = get_run(client, run["id"])
    assert done["status"] == "failed" and done["error"] == "No response arrived in time"


def test_an_answer_beats_a_later_timeout(client, anon):
    wid, run, approve, reject = start(client)
    anon.get(approve)
    assert expire(run["id"]) == 0
    assert get_run(client, run["id"])["status"] == "success"


def test_the_time_limit_is_validated(client):
    wid = approval_flow(client, seconds="30", unit="seconds")
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "at least one minute" in res["error"]
    assert client.post(f"/api/workflows/{approval_flow(client, seconds='x')}/run").json()["status"] == "failed"
    assert client.post(f"/api/workflows/{approval_flow(client, seconds='9', unit='days')}/run").json()["status"] == "failed"  # beyond 7 days


def test_only_one_response_wait_per_run_and_not_in_loops(client):
    nodes = [node("t", "trigger"), node("a", "callback", seconds="1", unit="days"), node("b", "callback", seconds="1", unit="days"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "a"), edge("a", "b"), edge("b", "o")])
    run = client.post(f"/api/workflows/{wid}/run").json()
    assert run["status"] == "waiting"
    link = next(s for s in run["steps"] if s["node_id"] == "a")["output"]["approve_url"].split(":5173", 1)[1]
    TestClient(main.app).get(link)  # answer the first one
    second = get_run(client, run["id"])
    assert second["status"] == "waiting"  # the second wait is a new pause after the first was answered
    loop = [node("t", "trigger"), node("i", "input", json="[1, 2]"), node("l", "loop", path=""), node("w", "callback", seconds="1", unit="days"), node("o", "output")]
    wid2 = make_flow(client, loop, [edge("t", "i"), edge("i", "l"), edge("l", "w", "each"), edge("l", "o", "done")])
    res = client.post(f"/api/workflows/{wid2}/run").json()
    assert res["status"] == "failed" and "loop" in res["error"]


def test_run_links_are_available_in_any_template_and_unknown_names_fail_clearly(client):
    wid = make_flow(client, [node("t", "trigger"), node("m", "template", template="{{run.resume_url}}"), node("o", "output")], [edge("t", "m"), edge("m", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success" and "/api/resume/" in res["output"] and "?decision" not in res["output"]
    bad = make_flow(client, [node("t", "trigger"), node("m", "template", template="{{run.nothing}}"), node("o", "output")], [edge("t", "m"), edge("m", "o")])
    assert "not available" in client.post(f"/api/workflows/{bad}/run").json()["error"]


def test_data_with_its_own_run_field_keeps_working(client):
    nodes = [node("t", "trigger"), node("i", "input", json='{"run": {"id": 12}}'), node("m", "template", template="{{run.id}}"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "m"), edge("m", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["output"] == "12"


def test_timer_waits_still_work_and_resume_with_their_data(client):
    nodes = [node("t", "trigger"), node("i", "input", json='{"keep": "me"}'), node("w", "wait", seconds="2", unit="hours"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "w"), edge("w", "o")])
    run = client.post(f"/api/workflows/{wid}/run").json()
    assert run["status"] == "waiting" and get_run(client, run["id"])["waiting_for"] is None
    expire(run["id"])
    assert get_run(client, run["id"])["output"] == {"keep": "me"}


def test_waiting_for_a_response_counts_toward_the_pending_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_PENDING_WAITS", 0)
    wid = approval_flow(client)
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "Too many runs are waiting" in res["error"]


def test_people_outside_a_project_cannot_answer_its_runs(client):
    pid = client.post("/api/projects", json={"name": "Approvals"}).json()["id"]
    uid = client.post("/api/users", json={"username": "outsider-cb", "password": "long-enough-pw", "role": "editor"}).json()["id"]
    wid = approval_flow(client)
    with main.conn() as db: db.execute("UPDATE workflows SET project_id=? WHERE id=?", (pid, wid))
    run = client.post(f"/api/workflows/{wid}/run").json()
    outsider = TestClient(main.app)
    assert outsider.post("/api/auth/login", json={"username": "outsider-cb", "password": "long-enough-pw"}).status_code == 200
    assert outsider.post(f"/api/executions/{run['id']}/respond", json={"decision": "approve"}).status_code == 404
    assert client.post(f"/api/executions/{run['id']}/respond", json={"decision": "approve"}).status_code == 200
    client.delete(f"/api/users/{uid}")
    with main.conn() as db: db.execute("UPDATE workflows SET project_id=NULL WHERE id=?", (wid,))
    client.delete(f"/api/projects/{pid}")
