import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def add_run(wid, status, ago_hours=1.0, seconds=2.0, error=None):
    start = datetime.now(timezone.utc) - timedelta(hours=ago_hours)
    with main.conn() as db:
        db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                   (f"stat-{uuid.uuid4()}", wid, status, start.isoformat(), (start + timedelta(seconds=seconds)).isoformat(), "null", "[]", error, "manual"))


def flows(client):
    a = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="Alpha")
    b = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="Beta")
    return a, b


def test_counts_rate_average_and_top_failures(client):
    a, b = flows(client)
    for _ in range(3): add_run(a, "success", seconds=2)
    add_run(a, "failed", seconds=4, error="x")
    add_run(b, "failed", seconds=0); add_run(b, "failed", seconds=0)
    add_run(b, "waiting")
    s = client.get("/api/stats?window=24h").json()
    assert (s["total"], s["success"], s["failed"], s["waiting"]) == (7, 3, 3, 1)
    assert s["failure_rate"] == 0.5 and s["average_seconds"] == round((2 * 3 + 4) / 6, 3)
    assert s["top_failing"][0] == {"workflow_id": b, "name": "Beta", "failed": 2} and s["top_failing"][1]["name"] == "Alpha"


def test_windows_exclude_older_runs_and_bad_windows_are_refused(client):
    a, _ = flows(client)
    add_run(a, "success", ago_hours=1); add_run(a, "failed", ago_hours=24 * 3); add_run(a, "failed", ago_hours=24 * 20)
    assert client.get("/api/stats?window=24h").json()["total"] == 1
    assert client.get("/api/stats?window=7d").json()["total"] == 2
    assert client.get("/api/stats?window=30d").json()["total"] == 3
    assert client.get("/api/stats?window=1y").status_code == 400


def test_an_empty_history_has_no_rate(client):
    s = client.get("/api/stats").json()
    assert s["total"] == 0 and s["failure_rate"] is None and s["average_seconds"] is None and s["top_failing"] == []


def test_stats_respect_project_visibility(client):
    a, b = flows(client)
    pid = client.post("/api/projects", json={"name": "Hidden team"}).json()["id"]
    client.put(f"/api/projects/assign/{b}", json={"project_id": pid})
    add_run(a, "success"); add_run(b, "failed")
    client.post("/api/users", json={"username": "stats-viewer", "password": "long-enough-pw", "role": "viewer"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "stats-viewer", "password": "long-enough-pw"}).status_code == 200
    seen = c.get("/api/stats").json()
    assert seen["failed"] == 0 and seen["total"] == 1 and seen["top_failing"] == []
    assert client.get("/api/stats").json()["failed"] == 1
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'stats-viewer')}")
    client.put(f"/api/projects/assign/{b}", json={"project_id": None})
    client.delete(f"/api/projects/{pid}")


def test_metrics_are_prometheus_text_for_owners_only(client):
    a, _ = flows(client)
    add_run(a, "success"); add_run(a, "failed")
    res = client.get("/api/metrics")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/plain")
    body = res.text
    assert 'flowpilot_runs{status="failed"} 1' in body and 'flowpilot_runs{status="success"} 1' in body and "flowpilot_queue_jobs 0" in body
    assert f'flowpilot_info{{version="{main.VERSION}"}} 1' in body and 'flowpilot_workflows{state="total"}' in body
    client.post("/api/users", json={"username": "metrics-admin", "password": "long-enough-pw", "role": "admin"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "metrics-admin", "password": "long-enough-pw"}).status_code == 200
    assert c.get("/api/metrics").status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'metrics-admin')}")
    assert TestClient(main.app).get("/api/metrics").status_code == 401
