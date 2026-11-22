import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app, summarize_step_timings
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _env(clean_state):
    with main.conn() as db: db.execute("DELETE FROM executions")
    yield
    with main.conn() as db:
        db.execute("DELETE FROM executions")
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM projects")
        db.execute("UPDATE workflows SET project_id=NULL")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def flow(client, name="Timing flow"):
    return make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name=name)


def step(nid, ms, status="success", label=None):
    return {"node_id": nid, "label": label or nid, "type": "http", "status": status, "duration_ms": ms}


def add_run(wid, steps, minutes_ago=1, status="success", trigger="manual"):
    s = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    with main.conn() as db:
        db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), wid, status, s.isoformat(), (s + timedelta(seconds=1)).isoformat(), "null", json.dumps(steps), None, trigger))


def timings(client, wid, **params):
    res = client.get(f"/api/workflows/{wid}/step-timings", params=params)
    assert res.status_code == 200, res.text
    return res.json()


# ------------------------------------------------------------ the summary itself
def test_no_runs_gives_no_steps():
    assert summarize_step_timings([]) == []


def test_steps_are_ordered_slowest_typical_time_first():
    out = summarize_step_timings([[step("a", 10), step("b", 500)], [step("a", 20), step("b", 700)]])
    assert [e["node_id"] for e in out] == ["b", "a"]
    assert out[0]["median_ms"] == 600 and out[0]["max_ms"] == 700 and out[0]["runs"] == 2


def test_one_outlier_does_not_decide_the_typical_time():
    out = summarize_step_timings([[step("a", 100)], [step("a", 110)], [step("a", 9000)]])
    assert out[0]["median_ms"] == 110 and out[0]["max_ms"] == 9000


def test_failures_are_counted_per_step():
    out = summarize_step_timings([[step("a", 5, "error")], [step("a", 5)], [step("a", 5, "error")]])
    assert out[0]["failed"] == 2 and out[0]["runs"] == 3


def test_waiting_steps_and_steps_without_a_time_do_not_distort_the_median():
    out = summarize_step_timings([[step("a", 0, "waiting")], [{"node_id": "a", "status": "success"}], [step("a", 40)]])
    e = out[0]
    assert e["runs"] == 2 and e["timed"] == 1 and e["median_ms"] == 40


def test_a_loop_step_is_counted_each_time_it_ran():
    out = summarize_step_timings([[step("loop", 10), step("loop", 30)]])
    assert out[0]["runs"] == 2 and out[0]["median_ms"] == 20


# ------------------------------------------------------------ the endpoint
def test_the_endpoint_reads_stored_runs(client):
    wid = flow(client)
    add_run(wid, [step("t", 1), step("slow", 900, label="Call the API")])
    add_run(wid, [step("t", 2), step("slow", 1100, label="Call the API")], minutes_ago=2)
    res = timings(client, wid)
    assert res["runs_used"] == 2 and res["steps"][0]["label"] == "Call the API" and res["steps"][0]["median_ms"] == 1000


def test_dry_and_unfinished_runs_are_left_out(client):
    wid = flow(client)
    add_run(wid, [step("a", 5)], trigger="dry")
    add_run(wid, [step("a", 5)], status="waiting")
    assert timings(client, wid)["runs_used"] == 0


def test_only_the_latest_runs_are_used(client):
    wid = flow(client)
    add_run(wid, [step("a", 5000)], minutes_ago=30)
    add_run(wid, [step("a", 10)], minutes_ago=1)
    res = timings(client, wid, runs=1)
    assert res["runs_used"] == 1 and res["steps"][0]["median_ms"] == 10


def test_bad_arguments_and_unknown_workflows(client, anon):
    wid = flow(client)
    assert client.get(f"/api/workflows/{wid}/step-timings", params={"runs": 0}).status_code == 400
    assert client.get(f"/api/workflows/{wid}/step-timings", params={"runs": 501}).status_code == 400
    assert client.get("/api/workflows/nope/step-timings").status_code == 404
    assert anon.get(f"/api/workflows/{wid}/step-timings").status_code == 401


def test_people_cannot_read_timings_of_workflows_they_cannot_see(client):
    pa = client.post("/api/projects", json={"name": "TimingAlpha"}).json()["id"]
    pb = client.post("/api/projects", json={"name": "TimingBeta"}).json()["id"]
    uid = client.post("/api/users", json={"username": "timinguser", "password": "long-enough-pw", "role": "viewer"}).json()["id"]
    client.put(f"/api/projects/{pa}", json={"user_ids": [uid]})
    mine = client.post("/api/workflows", params={"project_id": pa}, json={"name": "Mine", "description": "", "nodes": [], "edges": []}).json()["id"]
    theirs = client.post("/api/workflows", params={"project_id": pb}, json={"name": "Theirs", "description": "", "nodes": [], "edges": []}).json()["id"]
    me = TestClient(app)
    assert me.post("/api/auth/login", json={"username": "timinguser", "password": "long-enough-pw"}).status_code == 200
    assert me.get(f"/api/workflows/{mine}/step-timings").status_code == 200
    assert me.get(f"/api/workflows/{theirs}/step-timings").status_code in (403, 404)
