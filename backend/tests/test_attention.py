import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _env(clean_state):
    with main.conn() as db:
        db.execute("DELETE FROM executions")
        db.execute("DELETE FROM waits")
    yield
    with main.conn() as db:
        db.execute("DELETE FROM executions")
        db.execute("DELETE FROM waits")
        db.execute("DELETE FROM project_members")
        db.execute("DELETE FROM projects")
        db.execute("UPDATE workflows SET project_id=NULL")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def flow(client, name="Attention flow"):
    return make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name=name)


def failing_runs(wid, count=4):
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    with main.conn() as db:
        for i in range(count):
            s = start - timedelta(minutes=i)
            db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), wid, "failed", s.isoformat(), (s + timedelta(seconds=1)).isoformat(), "null", "[]", "boom", "manual"))


def approval(wid, kind="callback", status="pending"):
    eid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with main.conn() as db:
        db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                   (eid, wid, "waiting", now, None, "null", "[]", None, "manual"))
        db.execute("INSERT INTO waits (id,execution_id,workflow_id,node_id,value,resume_at,status,created_at,kind) VALUES (?,?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), eid, wid, "w", "{}", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), status, now, kind))
    return eid


def attention(client):
    res = client.get("/api/attention")
    assert res.status_code == 200, res.text
    return res.json()


def test_nothing_needs_attention_on_a_quiet_system(client):
    flow(client)
    assert attention(client) == {"count": 0, "items": []}


def test_a_failing_workflow_is_listed_with_its_reason(client):
    wid = flow(client)
    failing_runs(wid)
    got = attention(client)
    item = got["items"][0]
    assert got["count"] == 1 and item["kind"] == "health" and item["status"] == "failing" and item["workflow_id"] == wid and "failed" in item["message"]


def test_a_run_waiting_for_a_response_is_listed(client):
    wid = flow(client)
    eid = approval(wid)
    item = attention(client)["items"][0]
    assert item["kind"] == "approval" and item["execution_id"] == eid and item["workflow_id"] == wid and item["expires_at"]


def test_plain_timer_waits_and_finished_waits_are_not_listed(client):
    wid = flow(client)
    approval(wid, kind="timer")
    approval(wid, status="done")
    assert attention(client)["count"] == 0


def test_approvals_come_before_health_and_the_count_adds_up(client):
    a, b = flow(client, "Attn A"), flow(client, "Attn B")
    failing_runs(a)
    approval(b)
    got = attention(client)
    assert [i["kind"] for i in got["items"]] == ["approval", "health"] and got["count"] == 2


def test_login_is_needed(client, anon):
    assert anon.get("/api/attention").status_code == 401


def test_people_only_see_what_belongs_to_workflows_they_can_see(client):
    pa = client.post("/api/projects", json={"name": "AttnAlpha"}).json()["id"]
    pb = client.post("/api/projects", json={"name": "AttnBeta"}).json()["id"]
    uid = client.post("/api/users", json={"username": "attnuser", "password": "long-enough-pw", "role": "viewer"}).json()["id"]
    client.put(f"/api/projects/{pa}", json={"user_ids": [uid]})
    mine = client.post("/api/workflows", params={"project_id": pa}, json={"name": "Mine", "description": "", "nodes": [], "edges": []}).json()["id"]
    theirs = client.post("/api/workflows", params={"project_id": pb}, json={"name": "Theirs", "description": "", "nodes": [], "edges": []}).json()["id"]
    failing_runs(theirs); approval(theirs); approval(mine)
    me = TestClient(app)
    assert me.post("/api/auth/login", json={"username": "attnuser", "password": "long-enough-pw"}).status_code == 200
    got = me.get("/api/attention").json()
    assert got["count"] == 1 and got["items"][0]["workflow_id"] == mine
    assert client.get("/api/attention").json()["count"] == 3
