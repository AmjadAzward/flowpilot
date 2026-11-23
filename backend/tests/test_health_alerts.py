import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _env(clean_state):
    with main.conn() as db: db.execute("DELETE FROM executions")
    yield
    with main.conn() as db:
        db.execute("DELETE FROM executions")
        db.execute("DELETE FROM settings WHERE key IN ('default_error_workflow','health_alerts') OR key LIKE 'health_last:%'")


def handler(client):
    return make_flow(client, [node("t", "trigger"), node("m", "template", template="{{error}}"), node("o", "output")], [edge("t", "m"), edge("m", "o")], name="Alert handler")


def breaker(client, **cfg):
    return make_flow(client, [node("t", "trigger", **cfg), node("f", "fail", message="boom")], [edge("t", "f")], name="Breaker")


def health_runs(client, hid):
    return [r for r in client.get("/api/executions", params={"workflow_id": hid}).json() if str(r["output"]).startswith("Health changed")]


def old_failures(wid, count):
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    with main.conn() as db:
        for i in range(count):
            s = start - timedelta(minutes=i)
            db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), wid, "failed", s.isoformat(), (s + timedelta(seconds=1)).isoformat(), "null", "[]", "boom", "manual"))


def test_it_is_off_by_default_and_can_be_switched(client):
    assert client.get("/api/settings/health-alerts").json() == {"enabled": False}
    assert client.put("/api/settings/health-alerts", json={"enabled": True}).json() == {"enabled": True}
    assert client.get("/api/settings/health-alerts").json() == {"enabled": True}
    assert client.put("/api/settings/health-alerts", json={"enabled": False}).json() == {"enabled": False}


def test_nothing_happens_while_it_is_off(client):
    h = handler(client)
    client.put("/api/settings/error-workflow", json={"workflow_id": h})
    bad = breaker(client)
    for _ in range(4): client.post(f"/api/workflows/{bad}/run")
    assert health_runs(client, h) == []


def test_turning_failing_alerts_once_and_not_again_while_it_stays_failing(client):
    h = handler(client)
    client.put("/api/settings/error-workflow", json={"workflow_id": h})
    client.put("/api/settings/health-alerts", json={"enabled": True})
    bad = breaker(client)
    client.post(f"/api/workflows/{bad}/run")
    client.post(f"/api/workflows/{bad}/run")
    assert health_runs(client, h) == []  # two runs are too few to judge
    client.post(f"/api/workflows/{bad}/run")
    got = health_runs(client, h)
    assert len(got) == 1 and "failing" in got[0]["output"] and "3 of 3" in got[0]["output"]
    client.post(f"/api/workflows/{bad}/run")
    client.post(f"/api/workflows/{bad}/run")
    assert len(health_runs(client, h)) == 1


def test_the_alert_carries_the_health_details(client):
    h = make_flow(client, [node("t", "trigger"), node("m", "template", template="{{health.status}} after {{health.was}} on {{workflow.name}}"), node("o", "output")],
                  [edge("t", "m"), edge("m", "o")], name="Detail handler")
    client.put("/api/settings/error-workflow", json={"workflow_id": h})
    client.put("/api/settings/health-alerts", json={"enabled": True})
    bad = breaker(client)
    for _ in range(3): client.post(f"/api/workflows/{bad}/run")
    outputs = [r["output"] for r in client.get("/api/executions", params={"workflow_id": h}).json()]
    assert "failing after quiet on Breaker" in outputs


def test_switching_on_does_not_alert_about_trouble_that_was_already_there(client):
    h = handler(client)
    client.put("/api/settings/error-workflow", json={"workflow_id": h})
    bad = breaker(client)
    old_failures(bad, 4)
    client.put("/api/settings/health-alerts", json={"enabled": True})
    client.post(f"/api/workflows/{bad}/run")
    assert health_runs(client, h) == []


def test_it_uses_the_workflows_own_error_workflow_first(client):
    shared, own = handler(client), handler(client)
    client.put("/api/settings/error-workflow", json={"workflow_id": shared})
    client.put("/api/settings/health-alerts", json={"enabled": True})
    bad = breaker(client, error_workflow=own)
    for _ in range(3): client.post(f"/api/workflows/{bad}/run")
    assert len(health_runs(client, own)) == 1 and health_runs(client, shared) == []


def test_no_error_workflow_means_nothing_is_sent_and_nothing_breaks(client):
    client.put("/api/settings/health-alerts", json={"enabled": True})
    bad = breaker(client)
    for _ in range(4): res = client.post(f"/api/workflows/{bad}/run")
    assert res.status_code == 200 and res.json()["status"] == "failed"


def test_dry_runs_and_alert_runs_do_not_count_or_loop(client):
    h = handler(client)
    client.put("/api/settings/error-workflow", json={"workflow_id": h})
    client.put("/api/settings/health-alerts", json={"enabled": True})
    bad = breaker(client)
    for _ in range(4): client.post(f"/api/workflows/{bad}/run", params={"dry": "true"})
    assert health_runs(client, h) == []
    assert main.health_alerts_enabled() is True
