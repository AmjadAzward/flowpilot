import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import main, worker
from conftest import edge, make_flow, node

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean(clean_state, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_QUEUE", "1")
    yield
    with main.conn() as db:
        db.execute("DELETE FROM jobs")
        db.execute("DELETE FROM waits")


def hook(client, extra=None, template="got {{body.n}}"):
    nodes = [node("w", "webhook"), node("m", "template", template=template)] + (extra or []) + [node("o", "output")]
    edges = [edge("w", "m"), edge("m", "o")]
    wid = make_flow(client, nodes, edges)
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    return wid, client.get(f"/api/workflows/{wid}").json()["webhook_token"]


def jobs(status=None):
    with main.conn() as db:
        q, a = "SELECT * FROM jobs", ()
        if status: q, a = q + " WHERE status=?", (status,)
        return [dict(r) for r in db.execute(q, a).fetchall()]


def runs(client, wid):
    return client.get("/api/executions", params={"workflow_id": wid}).json()


# ---------------------------------------------------------------- queueing
def test_webhook_in_queue_mode_returns_immediately_and_runs_nothing_yet(client, anon):
    wid, token = hook(client)
    res = anon.post(f"/api/hooks/{token}", json={"n": 1})
    assert res.status_code == 202 and res.json()["status"] == "queued" and res.json()["job_id"]
    assert len(jobs("queued")) == 1 and runs(client, wid) == []


def test_a_worker_runs_the_job_and_records_the_execution(client, anon):
    wid, token = hook(client)
    anon.post(f"/api/hooks/{token}", json={"n": 7})
    assert worker.work(concurrency=2, once=True) == 1
    run = runs(client, wid)
    assert len(run) == 1 and run[0]["trigger"] == "webhook" and run[0]["output"] == "got 7"
    job = jobs()[0]
    assert job["status"] == "done" and job["execution_id"] == run[0]["id"]


def test_inline_mode_is_unchanged(client, anon, monkeypatch):
    monkeypatch.delenv("FLOWPILOT_QUEUE")
    wid, token = hook(client)
    res = anon.post(f"/api/hooks/{token}", json={"n": 2})
    assert res.status_code == 200 and res.json()["status"] == "success" and jobs() == []


def test_manual_runs_stay_inline_even_in_queue_mode(client):
    wid, _ = hook(client)
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success" and jobs() == []


def test_scheduled_runs_are_queued(client):
    wid = make_flow(client, [node("s", "schedule", mode="interval", minutes="5"), node("o", "output")], [edge("s", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    with main.conn() as db:
        db.execute("UPDATE workflows SET next_run_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), wid))
    assert main.scheduler_tick() == 1
    assert len(jobs("queued")) == 1 and runs(client, wid) == []
    worker.work(once=True)
    assert runs(client, wid)[0]["trigger"] == "schedule"


def test_queue_limit_returns_503(client, anon, monkeypatch):
    monkeypatch.setattr(main, "MAX_QUEUE", 2)
    _, token = hook(client)
    assert [anon.post(f"/api/hooks/{token}", json={}).status_code for _ in range(3)] == [202, 202, 503]


# ---------------------------------------------------------------- webhook replies through workers
def test_respond_node_webhooks_wait_for_a_worker(client, anon):
    wid = make_flow(client, [node("w", "webhook"), node("r", "respond", status="201", body='{"got": {{body.n}}}')], [edge("w", "r")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    stop = threading.Event()
    t = threading.Thread(target=worker.work, kwargs={"concurrency": 1, "poll": 0.05, "stop": stop}, daemon=True)
    t.start()
    try:
        res = anon.post(f"/api/hooks/{token}", json={"n": 5})
    finally:
        stop.set()
        t.join(5)
    assert res.status_code == 201 and res.json() == {"got": 5}


def test_respond_node_webhook_falls_back_to_202_when_no_worker_answers(client, anon, monkeypatch):
    monkeypatch.setattr(main, "WEBHOOK_WAIT_SECONDS", 0.3)
    wid = make_flow(client, [node("w", "webhook"), node("r", "respond", status="200", body="ok")], [edge("w", "r")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    assert anon.post(f"/api/hooks/{token}", json={}).status_code == 202


def test_form_submissions_get_a_thank_you_when_queued(client, anon):
    fields = json.dumps([{"name": "email", "label": "Email", "type": "email"}])
    wid = make_flow(client, [node("w", "webhook", form_fields=fields), node("o", "output")], [edge("w", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    res = anon.post(f"/api/hooks/{token}", data={"email": "a@example.com"}, headers={"accept": "text/html"})
    assert "Thank you" in res.text and len(jobs("queued")) == 1


# ---------------------------------------------------------------- claiming
def test_every_job_is_claimed_exactly_once_under_contention(client):
    wid, _ = hook(client)
    doc = main.get_workflow(wid)
    for i in range(40): main.enqueue(doc, "webhook", {"n": i})
    claimed, lock = [], threading.Lock()

    def grab(name):
        while True:
            job = main.claim_job(name)
            if job is None: return
            with lock: claimed.append(job["id"])

    threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(6)]
    for t in threads: t.start()
    for t in threads: t.join(20)
    assert len(claimed) == 40 and len(set(claimed)) == 40


def test_jobs_run_oldest_first(client):
    wid, _ = hook(client)
    doc = main.get_workflow(wid)
    ids = [main.enqueue(doc, "webhook", {"n": i}) for i in range(3)]
    order = []
    while (j := main.claim_job("w")): order.append(j["id"])
    assert order == ids


def test_a_separate_worker_process_runs_queued_jobs(client, anon):
    wid, token = hook(client)
    for n in range(5): anon.post(f"/api/hooks/{token}", json={"n": n})
    env = {**os.environ, "FLOWPILOT_QUEUE": "1"}
    out = subprocess.run([sys.executable, "-m", "app.worker", "--once", "--concurrency", "3"], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "stopped after 5 jobs" in out.stdout, out.stdout + out.stderr
    assert sorted(r["output"] for r in runs(client, wid)) == [f"got {n}" for n in range(5)]
    assert len(jobs("done")) == 5


def test_two_worker_processes_split_the_work_without_duplicates(client, anon):
    wid, token = hook(client)
    for n in range(12): anon.post(f"/api/hooks/{token}", json={"n": n})
    env = {**os.environ, "FLOWPILOT_QUEUE": "1"}
    procs = [subprocess.Popen([sys.executable, "-m", "app.worker", "--once", "--concurrency", "2"], cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    for p in procs: p.communicate(timeout=120)
    got = sorted(r["output"] for r in runs(client, wid))
    assert got == sorted(f"got {n}" for n in range(12)), got  # all ran, none twice


# ---------------------------------------------------------------- failures and recovery
def test_a_job_for_a_deleted_workflow_fails_cleanly(client):
    wid, _ = hook(client)
    main.enqueue(main.get_workflow(wid), "webhook", {})
    client.delete(f"/api/workflows/{wid}")
    worker.work(once=True)
    job = jobs()[0]
    assert job["status"] == "failed" and "not found" in job["error"].lower()


def test_a_failing_workflow_is_a_finished_job_with_a_failed_run(client, anon):
    wid = make_flow(client, [node("w", "webhook"), node("f", "fail", message="boom")], [edge("w", "f")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    anon.post(f"/api/hooks/{token}", json={})
    worker.work(once=True)
    assert runs(client, wid)[0]["status"] == "failed" and jobs()[0]["status"] == "done"


def test_jobs_from_a_vanished_worker_are_closed_not_rerun(client):
    wid, _ = hook(client)
    main.enqueue(main.get_workflow(wid), "webhook", {"n": 1})
    job = main.claim_job("dead-worker")
    old = (datetime.now(timezone.utc) - timedelta(seconds=main.STALE_JOB_SECONDS + 60)).isoformat()
    with main.conn() as db: db.execute("UPDATE jobs SET claimed_at=? WHERE id=?", (old, job["id"]))
    assert main.recover_jobs() == 1
    assert jobs()[0]["status"] == "failed" and "worker stopped" in jobs()[0]["error"]
    assert runs(client, wid) == [] and main.claim_job("w") is None


def test_fresh_running_jobs_are_left_alone(client):
    wid, _ = hook(client)
    main.enqueue(main.get_workflow(wid), "webhook", {})
    main.claim_job("busy")
    assert main.recover_jobs() == 0 and jobs()[0]["status"] == "running"


def test_waiting_runs_are_resumed_by_workers_in_queue_mode(client):
    nodes = [node("t", "trigger"), node("w", "wait", seconds="2", unit="minutes"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "w"), edge("w", "o")])
    run = client.post(f"/api/workflows/{wid}/run").json()
    assert run["status"] == "waiting"
    with main.conn() as db: db.execute("UPDATE waits SET resume_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),))
    worker.work(once=True)
    assert runs(client, wid)[0]["status"] == "success"


# ---------------------------------------------------------------- stats and housekeeping
def test_queue_stats_endpoint(client, anon):
    assert client.get("/api/queue").json() == {"mode": "queue", "queued": 0, "running": 0, "failed": 0, "done": 0}
    _, token = hook(client)
    anon.post(f"/api/hooks/{token}", json={})
    assert client.get("/api/queue").json()["queued"] == 1
    assert anon.get("/api/queue").status_code == 401


def test_old_finished_jobs_are_pruned(client):
    wid, _ = hook(client)
    main.enqueue(main.get_workflow(wid), "webhook", {})
    worker.work(once=True)
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with main.conn() as db: db.execute("UPDATE jobs SET finished_at=?", (old,))
    main.prune_executions()
    assert jobs() == []


@pytest.mark.skipif(bool(main.DATABASE_URL), reason="WAL is a SQLite setting")
def test_the_database_uses_wal_for_concurrent_processes():
    with main.conn() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
