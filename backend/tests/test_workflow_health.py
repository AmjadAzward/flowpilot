import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app, judge_health, summarize_runs
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


def flow(client, name="Health flow"):
    return make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name=name)


def add_runs(wid, hours_ago, ok, seconds=1.0, count=1, trigger="manual"):
    start = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    with main.conn() as db:
        for i in range(count):
            s = start - timedelta(minutes=i)
            db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), wid, "success" if ok else "failed", s.isoformat(), (s + timedelta(seconds=seconds)).isoformat(), "null", "[]", None if ok else "boom", trigger))


def health(client, wid):
    res = client.get("/api/stats/health")
    assert res.status_code == 200, res.text
    return next(w for w in res.json()["workflows"] if w["workflow_id"] == wid)


# ------------------------------------------------------------ the judgement itself
def s(runs):
    return summarize_runs(runs)


def test_a_workflow_with_no_runs_is_quiet():
    assert judge_health(s([]), s([]))[0] == "quiet"


def test_a_few_runs_are_never_judged_however_bad():
    assert judge_health(s([(False, 1)] * 2), s([(True, 1)] * 20))[0] == "quiet"


def test_mostly_failing_runs_are_failing_whatever_the_past_was():
    assert judge_health(s([(False, 1)] * 3 + [(True, 1)]), s([]))[0] == "failing"
    assert judge_health(s([(False, 1)] * 3), s([(False, 1)] * 10))[0] == "failing"  # it was always bad, still reported as failing


def test_failing_much_more_than_usual_is_worse():
    status, reason = judge_health(s([(False, 1)] * 2 + [(True, 1)] * 6), s([(True, 1)] * 20))  # 25% now, 0% before
    assert status == "worse" and "25%" in reason and "0%" in reason


def test_a_small_rise_is_still_healthy():
    assert judge_health(s([(False, 1)] * 1 + [(True, 1)] * 9), s([(True, 1)] * 10))[0] == "healthy"  # 10% against 0%


def test_much_slower_than_usual_is_flagged_but_only_by_a_real_margin():
    assert judge_health(s([(True, 6.0)] * 4), s([(True, 2.0)] * 10))[0] == "slower"
    assert judge_health(s([(True, 0.3)] * 4), s([(True, 0.1)] * 10))[0] == "healthy"  # three times slower but only 0.2 s


def test_slowness_needs_a_baseline():
    assert judge_health(s([(True, 60.0)] * 4), s([(True, 1.0)] * 2))[0] == "healthy"


def test_the_median_ignores_one_outlier():
    assert summarize_runs([(True, 1.0), (True, 1.0), (True, 500.0)])["median_seconds"] == 1.0


# ------------------------------------------------------------ the endpoint
def test_the_endpoint_reads_real_runs(client):
    wid = flow(client)
    add_runs(wid, hours_ago=2, ok=False, count=4)
    h = health(client, wid)
    assert h["status"] == "failing" and h["recent"]["runs"] == 4 and h["recent"]["failed"] == 4


def test_runs_older_than_a_day_form_the_baseline(client):
    wid = flow(client)
    add_runs(wid, hours_ago=48, ok=True, count=10)
    add_runs(wid, hours_ago=3, ok=False, count=3)
    add_runs(wid, hours_ago=2, ok=True, count=7)
    h = health(client, wid)
    assert h["baseline"]["runs"] == 10 and h["baseline"]["failure_rate"] == 0 and h["recent"]["runs"] == 10 and h["status"] == "worse"


def test_runs_older_than_the_baseline_week_are_ignored(client):
    wid = flow(client)
    add_runs(wid, hours_ago=24 * 20, ok=False, count=10)
    assert health(client, wid)["baseline"]["runs"] == 0


def test_dry_runs_waiting_runs_and_running_runs_are_not_counted(client):
    wid = flow(client)
    add_runs(wid, hours_ago=1, ok=False, count=5, trigger="dry")
    with main.conn() as db:
        db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), wid, "waiting", datetime.now(timezone.utc).isoformat(), None, "null", "[]", None, "manual"))
    assert health(client, wid)["recent"]["runs"] == 0


def test_every_visible_workflow_is_listed_once_and_login_is_needed(client, anon):
    a, b = flow(client, "Health A"), flow(client, "Health B")
    ids = [w["workflow_id"] for w in client.get("/api/stats/health").json()["workflows"]]
    assert a in ids and b in ids and len(ids) == len(set(ids))
    assert anon.get("/api/stats/health").status_code == 401


def test_people_only_see_the_health_of_workflows_they_can_see(client):
    pa = client.post("/api/projects", json={"name": "HealthAlpha"}).json()["id"]
    pb = client.post("/api/projects", json={"name": "HealthBeta"}).json()["id"]
    uid = client.post("/api/users", json={"username": "healthuser", "password": "long-enough-pw", "role": "viewer"}).json()["id"]
    client.put(f"/api/projects/{pa}", json={"user_ids": [uid]})
    mine = client.post("/api/workflows", params={"project_id": pa}, json={"name": "Mine", "description": "", "nodes": [], "edges": []}).json()["id"]
    theirs = client.post("/api/workflows", params={"project_id": pb}, json={"name": "Theirs", "description": "", "nodes": [], "edges": []}).json()["id"]
    me = TestClient(app)
    assert me.post("/api/auth/login", json={"username": "healthuser", "password": "long-enough-pw"}).status_code == 200
    ids = [w["workflow_id"] for w in me.get("/api/stats/health").json()["workflows"]]
    assert mine in ids and theirs not in ids
