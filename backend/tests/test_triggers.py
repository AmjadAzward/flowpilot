import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import main
from conftest import edge, make_flow, node


def schedule_flow(client, **cfg):
    data = {"mode": "interval", "minutes": "30", **cfg}
    return make_flow(client,
        [node("s", "schedule", **data), node("m", "template", template="ran at {{scheduled_at}}"), node("o", "output")],
        [edge("s", "m"), edge("m", "o")])


def webhook_flow(client, template="got {{body.name}} via {{method}}", **data):
    return make_flow(client,
        [node("w", "webhook", **data), node("m", "template", template=template), node("o", "output")],
        [edge("w", "m"), edge("m", "o")])


def activate(client, wid, on=True):
    return client.post(f"/api/workflows/{wid}/active", json={"active": on})


def token_of(client, wid):
    return client.get(f"/api/workflows/{wid}").json()["webhook_token"]


def runs_of(client, wid):
    return client.get("/api/executions", params={"workflow_id": wid}).json()


@pytest.fixture(autouse=True)
def _reset(session_cookie):
    """Each test starts with only the seeded workflow, so scheduler ticks see nothing else."""
    def wipe():
        main._hook_hits.clear()
        with main.conn() as db:
            db.execute("DELETE FROM workflows WHERE id != 'demo-workflow'")
            db.execute("DELETE FROM executions")
    wipe()
    yield
    wipe()


# ---------------------------------------------------------------- schedule maths
def test_next_run_interval():
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert main.next_run_after({"mode": "interval", "minutes": 30}, base) == base + timedelta(minutes=30)


def test_next_run_daily_today_or_tomorrow():
    local = datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)
    cfg = {"mode": "daily", "hour": 9, "minute": 30}
    same_day = main.next_run_after(cfg, local).astimezone()
    assert (same_day.day, same_day.hour, same_day.minute) == (local.day, 9, 30)
    later = local.replace(hour=10)
    next_day = main.next_run_after(cfg, later)
    assert next_day > later and (next_day.astimezone().hour, next_day.astimezone().minute) == (9, 30)
    assert next_day - later < timedelta(hours=25)


@pytest.mark.parametrize("data", [
    {"mode": "interval", "minutes": "0"}, {"mode": "interval", "minutes": "abc"}, {"mode": "interval", "minutes": "20000"},
    {"mode": "daily", "time": "25:00"}, {"mode": "daily", "time": "9am"}, {"mode": "weird"},
])
def test_validate_schedule_rejects_bad_config(data):
    with pytest.raises(ValueError):
        main.validate_schedule(data)


def test_validate_schedule_accepts_good_config():
    assert main.validate_schedule({"mode": "interval", "minutes": "15.0"}) == {"mode": "interval", "minutes": 15}
    assert main.validate_schedule({"mode": "daily", "time": "7:05"}) == {"mode": "daily", "hour": 7, "minute": 5}
    assert main.validate_schedule({}) == {"mode": "interval", "minutes": 15}


# ---------------------------------------------------------------- activation
def test_manual_workflow_cannot_be_activated(client):
    res = activate(client, "demo-workflow")
    assert res.status_code == 400 and "Schedule or Webhook" in res.json()["detail"]


def test_activate_and_deactivate_schedule(client):
    wid = schedule_flow(client)
    res = activate(client, wid).json()
    assert res["active"] is True
    due = datetime.fromisoformat(res["next_run_at"])
    assert timedelta(minutes=29) < due - datetime.now(timezone.utc) < timedelta(minutes=31)
    listed = next(w for w in client.get("/api/workflows").json() if w["id"] == wid)
    assert listed["active"] is True and listed["trigger"] == "schedule" and listed["next_run_at"]
    off = activate(client, wid, False).json()
    assert off["active"] is False and off["next_run_at"] is None


def test_activate_rejects_invalid_schedule(client):
    wid = schedule_flow(client, minutes="0")
    assert activate(client, wid).status_code == 400


def test_activate_rejects_multiple_triggers(client):
    wid = make_flow(client, [node("a", "schedule"), node("b", "webhook")], [])
    assert activate(client, wid).status_code == 400


def test_activate_missing_workflow_404(client):
    assert activate(client, "nope").status_code == 404


def test_saving_without_schedule_node_deactivates(client):
    wid = schedule_flow(client)
    activate(client, wid)
    body = {"name": "t", "description": "", "nodes": [node("t", "trigger")], "edges": []}
    res = client.put(f"/api/workflows/{wid}", json=body).json()
    assert res["active"] is False and "Schedule or Webhook" in res["deactivated_reason"]
    assert client.get(f"/api/workflows/{wid}").json()["active"] is False


def test_saving_schedule_change_recomputes_next_run(client):
    wid = schedule_flow(client, minutes="30")
    activate(client, wid)
    doc = client.get(f"/api/workflows/{wid}").json()
    doc["nodes"][0]["data"]["minutes"] = "120"
    client.put(f"/api/workflows/{wid}", json=doc)
    nxt = datetime.fromisoformat(client.get(f"/api/workflows/{wid}").json()["next_run_at"])
    assert nxt - datetime.now(timezone.utc) > timedelta(minutes=110)


def test_duplicate_is_inactive_with_new_token(client):
    wid = webhook_flow(client)
    activate(client, wid)
    copy = client.post(f"/api/workflows/{wid}/duplicate").json()["id"]
    doc = client.get(f"/api/workflows/{copy}").json()
    assert doc["active"] is False and doc["webhook_token"] != token_of(client, wid)


# ---------------------------------------------------------------- scheduler
def test_scheduler_runs_due_workflow_and_advances(client):
    wid = schedule_flow(client)
    activate(client, wid)
    soon = datetime.now(timezone.utc) + timedelta(minutes=31)
    assert main.scheduler_tick(soon) == 1
    runs = runs_of(client, wid)
    assert len(runs) == 1 and runs[0]["trigger"] == "schedule" and runs[0]["status"] == "success"
    assert runs[0]["output"].startswith("ran at ")
    nxt = datetime.fromisoformat(client.get(f"/api/workflows/{wid}").json()["next_run_at"])
    assert nxt > soon
    assert main.scheduler_tick(soon) == 0  # not due again


def test_scheduler_ignores_not_due_and_inactive(client):
    wid = schedule_flow(client)
    assert main.scheduler_tick(datetime.now(timezone.utc) + timedelta(days=2)) == 0  # inactive
    activate(client, wid)
    assert main.scheduler_tick() == 0  # active but not yet due
    assert runs_of(client, wid) == []


def test_scheduler_catches_up_once_after_downtime(client):
    wid = schedule_flow(client, minutes="10")
    activate(client, wid)
    with main.conn() as db:
        db.execute("UPDATE workflows SET next_run_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", wid))
    assert main.scheduler_tick() == 1
    assert main.scheduler_tick() == 0
    assert len(runs_of(client, wid)) == 1


def test_scheduler_survives_a_broken_workflow(client):
    bad = schedule_flow(client)
    good = schedule_flow(client)
    activate(client, bad)
    activate(client, good)
    with main.conn() as db:
        doc = json.loads(db.execute("SELECT definition FROM workflows WHERE id=?", (bad,)).fetchone()["definition"])
        doc["nodes"][0]["data"]["minutes"] = "garbage"
        db.execute("UPDATE workflows SET definition=? WHERE id=?", (json.dumps(doc), bad))
    assert main.scheduler_tick(datetime.now(timezone.utc) + timedelta(minutes=45)) == 1
    assert len(runs_of(client, good)) == 1 and runs_of(client, bad) == []


def test_scheduler_deactivates_workflow_that_lost_its_schedule_node(client):
    wid = schedule_flow(client)
    activate(client, wid)
    with main.conn() as db:
        doc = json.loads(db.execute("SELECT definition FROM workflows WHERE id=?", (wid,)).fetchone()["definition"])
        doc["nodes"][0]["type"] = "trigger"
        db.execute("UPDATE workflows SET definition=? WHERE id=?", (json.dumps(doc), wid))
    assert main.scheduler_tick(datetime.now(timezone.utc) + timedelta(hours=1)) == 0
    assert client.get(f"/api/workflows/{wid}").json()["active"] is False


def test_scheduler_loop_exits_when_stopped():
    stop = threading.Event()
    stop.set()
    main.scheduler_loop(stop)  # returns immediately instead of looping forever


# ---------------------------------------------------------------- webhooks
def test_webhook_runs_with_json_body_without_login(client, anon):
    wid = webhook_flow(client)
    activate(client, wid)
    res = anon.post(f"/api/hooks/{token_of(client, wid)}", json={"name": "Ada"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success" and set(body) == {"status", "execution_id"}  # no output leaked
    run = runs_of(client, wid)[0]
    assert run["trigger"] == "webhook" and run["output"] == "got Ada via POST" and run["id"] == body["execution_id"]


def test_webhook_get_exposes_query_string(client, anon):
    wid = webhook_flow(client, template="x={{query.x}} {{method}}")
    activate(client, wid)
    assert anon.get(f"/api/hooks/{token_of(client, wid)}?x=42").status_code == 200
    assert runs_of(client, wid)[0]["output"] == "x=42 GET"


def test_webhook_plain_text_body(client, anon):
    wid = webhook_flow(client, template="{{body}}|{{content_type}}")
    activate(client, wid)
    anon.post(f"/api/hooks/{token_of(client, wid)}", content="hello there", headers={"Content-Type": "text/plain"})
    assert runs_of(client, wid)[0]["output"] == "hello there|text/plain"


def test_webhook_inactive_unknown_and_wrong_type_return_404(client, anon):
    wid = webhook_flow(client)
    tok = token_of(client, wid)
    assert anon.post(f"/api/hooks/{tok}", json={}).status_code == 404  # not active yet
    activate(client, wid)
    assert anon.post("/api/hooks/not-a-token", json={}).status_code == 404
    sched = schedule_flow(client)
    activate(client, sched)
    assert anon.post(f"/api/hooks/{token_of(client, sched)}", json={}).status_code == 404  # not a webhook workflow


def test_webhook_rejects_oversized_payload(client, anon):
    wid = webhook_flow(client)
    activate(client, wid)
    res = anon.post(f"/api/hooks/{token_of(client, wid)}", content="x" * (main.WEBHOOK_MAX_BODY + 1))
    assert res.status_code == 413


def test_webhook_rate_limit(client, anon, monkeypatch):
    monkeypatch.setattr(main, "WEBHOOK_RATE_PER_MINUTE", 3)
    wid = webhook_flow(client)
    activate(client, wid)
    url = f"/api/hooks/{token_of(client, wid)}"
    assert [anon.post(url, json={}).status_code for _ in range(4)] == [200, 200, 200, 429]


def test_unknown_tokens_are_not_tracked_by_rate_limiter(anon):
    for i in range(5):
        anon.post(f"/api/hooks/bogus-{i}", json={})
    assert main._hook_hits == {}


def test_failing_webhook_workflow_returns_500_and_is_logged(client, anon):
    wid = make_flow(client, [node("w", "webhook"), node("i", "input", json="{oops")], [edge("w", "i")])
    activate(client, wid)
    res = anon.post(f"/api/hooks/{token_of(client, wid)}", json={})
    assert res.status_code == 500 and res.json()["status"] == "failed"
    assert runs_of(client, wid)[0]["status"] == "failed"


def test_rotate_requires_login_and_invalidates_old_url(client, anon):
    wid = webhook_flow(client)
    activate(client, wid)
    old = token_of(client, wid)
    assert anon.post(f"/api/workflows/{wid}/webhook/rotate").status_code == 401
    new = client.post(f"/api/workflows/{wid}/webhook/rotate").json()["webhook_token"]
    assert new != old
    assert anon.post(f"/api/hooks/{old}", json={}).status_code == 404
    assert anon.post(f"/api/hooks/{new}", json={"name": "x"}).status_code == 200


def test_deleting_workflow_kills_its_webhook(client, anon):
    wid = webhook_flow(client)
    activate(client, wid)
    tok = token_of(client, wid)
    client.delete(f"/api/workflows/{wid}")
    assert anon.post(f"/api/hooks/{tok}", json={}).status_code == 404


def test_tokens_are_unique_and_long(client):
    a, b = webhook_flow(client), webhook_flow(client)
    ta, tb = token_of(client, a), token_of(client, b)
    assert ta != tb and len(ta) >= 30


# ---------------------------------------------------------------- manual test runs
def test_manual_run_of_webhook_workflow_uses_sample_payload(client):
    wid = webhook_flow(client, sample='{"name":"Zed"}')
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success" and res["output"] == "got Zed via POST" and res["trigger"] == "manual"


def test_manual_run_with_invalid_sample_fails_cleanly(client):
    wid = webhook_flow(client, sample="{nope")
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "Invalid JSON" in res["error"]


def test_manual_run_of_schedule_workflow_gets_a_test_event(client):
    wid = schedule_flow(client)
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success" and res["output"].startswith("ran at ")


def test_manual_run_accepts_explicit_input(client):
    wid = make_flow(client, [node("t", "trigger"), node("n", "note"), node("o", "output")], [edge("t", "n"), edge("n", "o")])
    res = client.post(f"/api/workflows/{wid}/run", json={"input": {"a": 1}}).json()
    assert res["output"] == {"a": 1}
    assert client.post(f"/api/workflows/{wid}/run").json()["output"] is None


# ---------------------------------------------------------------- migration
def test_old_database_is_migrated_in_place(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE workflows (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', definition TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    db.execute("CREATE TABLE executions (id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, output TEXT, steps TEXT, error TEXT)")
    db.execute("INSERT INTO workflows VALUES ('w1','Old','', '{\"nodes\":[],\"edges\":[]}','t','t')")
    db.execute("INSERT INTO executions VALUES ('e1','w1','success','t','t','null','[]',NULL)")
    db.commit()
    db.close()
    monkeypatch.setattr(main, "DB_PATH", path)
    main.initialize()
    main.initialize()  # idempotent
    with main.conn() as c:
        wf = c.execute("SELECT * FROM workflows WHERE id='w1'").fetchone()
        ex = c.execute("SELECT * FROM executions WHERE id='e1'").fetchone()
    assert wf["active"] == 0 and wf["webhook_token"] and len(wf["webhook_token"]) >= 30
    assert ex["trigger_kind"] == "manual"
