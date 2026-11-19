import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    main.LIVE_RUNS.clear()
    with main.conn() as db:
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM projects")
        db.execute("UPDATE workflows SET project_id=NULL")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def slow_flow(client, seconds="1"):
    return make_flow(client, [node("t", "trigger"), node("i", "input", json='{"a": 1}'), node("d", "delay", seconds=seconds), node("o", "output")], [edge("t", "i"), edge("i", "d"), edge("d", "o")])


def poll(client, run_id, until, limit=15):
    seen = []
    t0 = time.time()
    while time.time() - t0 < limit:
        snap = client.get(f"/api/live/{run_id}").json()
        seen.append(snap)
        if until(snap): return seen
        time.sleep(0.05)
    raise AssertionError("timed out waiting for the run")


def test_a_live_run_returns_at_once_and_shows_steps_while_it_works(client):
    wid = slow_flow(client, "1")
    t0 = time.time()
    started = client.post(f"/api/workflows/{wid}/run", params={"live": "true"}).json()
    assert started["status"] == "running" and started["live"] is True and time.time() - t0 < 0.8
    seen = poll(client, started["id"], lambda s: s["result"] is not None)
    midway = [s for s in seen if s["status"] == "running" and any(st["node_id"] == "d" for st in s["steps"])]
    assert midway, "the delay step should be visible while it is still running"
    delay_step = next(st for st in midway[0]["steps"] if st["node_id"] == "d")
    assert "status" not in delay_step  # started, not finished
    final = seen[-1]
    assert final["status"] == "success" and final["result"]["id"] == started["id"]
    assert [s["status"] for s in final["steps"]] == ["success"] * 4
    # the run was stored like any other
    stored = client.get("/api/executions").json()
    assert any(e["id"] == started["id"] and e["status"] == "success" for e in stored)


def test_a_failing_live_run_reports_failure(client):
    wid = make_flow(client, [node("t", "trigger"), node("f", "fail", message="stop here"), node("o", "output")], [edge("t", "f"), edge("f", "o")])
    started = client.post(f"/api/workflows/{wid}/run", params={"live": "true"}).json()
    final = poll(client, started["id"], lambda s: s["result"] is not None)[-1]
    assert final["status"] == "failed" and "stop here" in final["result"]["error"]


def test_a_normal_run_still_returns_the_whole_result(client):
    wid = slow_flow(client, "0.01")
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success" and len(res["steps"]) == 4 and "live" not in res


def test_unknown_run_ids_are_not_found(client):
    assert client.get("/api/live/does-not-exist").status_code == 404


def test_live_snapshots_hide_credential_values():
    rec = {"steps": [{"node_id": "x", "output": "token is supersecretvalue1 ok"}], "secrets": {"API": "supersecretvalue1"}}
    out = main.live_steps(rec)
    assert "supersecretvalue1" not in str(out) and "[hidden]" in str(out)
    assert main.live_steps({"steps": None, "secrets": {}}) == []


def test_too_many_live_runs_is_refused_and_finished_ones_expire(client, monkeypatch):
    wid = slow_flow(client, "0.01")
    monkeypatch.setattr(main, "MAX_LIVE_RUNS", 1)
    first = client.post(f"/api/workflows/{wid}/run", params={"live": "true"}).json()
    assert client.post(f"/api/workflows/{wid}/run", params={"live": "true"}).status_code == 429
    poll(client, first["id"], lambda s: s["result"] is not None)
    monkeypatch.setattr(main, "LIVE_KEEP_SECONDS", -1)
    assert client.post(f"/api/workflows/{wid}/run", params={"live": "true"}).status_code == 200


def test_people_outside_the_project_cannot_watch_a_run(client):
    proj = client.post("/api/projects", json={"name": "Secret"}).json()["id"]
    res = client.post("/api/users", json={"username": "outsider", "password": "long-enough-pw", "role": "editor"})
    assert res.status_code == 201
    wid = slow_flow(client, "0.5")
    with main.conn() as db: db.execute("UPDATE workflows SET project_id=? WHERE id=?", (proj, wid))
    started = client.post(f"/api/workflows/{wid}/run", params={"live": "true"}).json()
    other = TestClient(app)
    assert other.post("/api/auth/login", json={"username": "outsider", "password": "long-enough-pw"}).status_code == 200
    assert other.get(f"/api/live/{started['id']}").status_code == 404
    assert client.get(f"/api/live/{started['id']}").status_code == 200
    poll(client, started["id"], lambda s: s["result"] is not None)
