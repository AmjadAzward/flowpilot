import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM settings")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def run(client, nodes, edges):
    wid = make_flow(client, nodes, edges)
    return client.post(f"/api/workflows/{wid}/run").json()


def seed_runs(n, days_ago, status="success"):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with main.conn() as db:
        for i in range(n):
            db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                       (f"seed-{days_ago}-{status}-{i}-{ts}", "demo-workflow", status, ts, ts, "null", "[]", None, "manual"))


def count(status=None):
    with main.conn() as db:
        q, a = "SELECT COUNT(*) c FROM executions", ()
        if status: q, a = q + " WHERE status=?", (status,)
        return db.execute(q, a).fetchone()["c"]


# ---------------------------------------------------------------- retention
def test_settings_defaults_validation_and_roles(client):
    assert client.get("/api/settings").json()["retention_days"] == 90
    assert client.put("/api/settings", json={"retention_days": -1, "max_executions": 10}).status_code == 422
    assert client.put("/api/settings", json={"retention_days": 30, "max_executions": 999999}).status_code == 422
    assert client.put("/api/settings", json={"retention_days": 30, "max_executions": 100}).json()["retention_days"] == 30
    assert client.get("/api/settings").json()["max_executions"] == 100


def test_old_runs_are_pruned_but_waiting_runs_never(client):
    seed_runs(3, 200)
    seed_runs(2, 1)
    seed_runs(1, 400, "waiting")
    res = client.put("/api/settings", json={"retention_days": 30, "max_executions": 0}).json()
    assert res["removed"] == 3
    assert count() == 3 and count("waiting") == 1


def test_run_count_cap_keeps_the_newest(client):
    seed_runs(5, 3)
    seed_runs(5, 1)
    client.put("/api/settings", json={"retention_days": 0, "max_executions": 4})
    assert count() == 4
    with main.conn() as db:
        assert db.execute("SELECT MIN(started_at) m FROM executions").fetchone()["m"] > (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()


def test_zero_means_keep_everything_and_prune_now_works(client):
    seed_runs(4, 500)
    client.put("/api/settings", json={"retention_days": 0, "max_executions": 0})
    assert client.post("/api/settings/prune").json()["removed"] == 0 and count() == 4


def test_pruning_removes_orphaned_waits(client):
    with main.conn() as db:
        db.execute("INSERT INTO waits (id,execution_id,workflow_id,node_id,value,resume_at,status,created_at) VALUES ('w1','gone','demo-workflow','n','1','2099-01-01','pending','x')")
    main.prune_executions()
    with main.conn() as db:
        assert db.execute("SELECT COUNT(*) c FROM waits WHERE id='w1'").fetchone()["c"] == 0


# ---------------------------------------------------------------- backup / restore
def test_workflow_backup_roundtrip(client):
    make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="Backup me")
    res = client.get("/api/backup/workflows")
    assert res.status_code == 200 and "attachment" in res.headers["content-disposition"]
    data = res.json()
    assert data["format"] == "flowpilot-backup" and any(w["name"] == "Backup me" for w in data["workflows"])
    assert "webhook_token" not in res.text and "pbkdf2" not in res.text
    out = client.post("/api/backup/restore", json=data).json()
    assert len(out["created"]) == len(data["workflows"]) and out["skipped"] == []
    names = [w["name"] for w in client.get("/api/workflows").json()]
    assert names.count("Backup me") == 2
    restored = client.get(f"/api/workflows/{out['created'][0]['id']}").json()
    assert restored["active"] is False


def test_restore_skips_bad_items_and_rejects_foreign_files(client):
    good = {"name": "ok", "nodes": [node("t", "trigger")], "edges": []}
    bad = {"name": "bad", "nodes": [node("t", "nonsense")], "edges": []}
    out = client.post("/api/backup/restore", json={"format": "flowpilot-backup", "workflows": [good, bad, {"name": "x", "nodes": "no", "edges": []}]}).json()
    assert [c["name"] for c in out["created"]] == ["ok"] and len(out["skipped"]) == 2
    assert client.post("/api/backup/restore", json={"format": "other", "workflows": []}).status_code == 400
    assert client.post("/api/backup/restore", json={"format": "flowpilot-backup", "workflows": [good] * 201}).status_code == 422


def test_database_backup_is_a_clean_copy_without_sessions(client, tmp_path):
    res = client.get("/api/backup/database")
    assert res.status_code == 200
    f = tmp_path / "b.db"
    f.write_bytes(res.content)
    db = sqlite3.connect(f)
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] >= 1
    db.close()


def test_backups_are_owner_only(client):
    client.post("/api/users", json={"username": "adminy", "password": "long-enough-pw", "role": "admin"})
    a = TestClient(app)
    a.post("/api/auth/login", json={"username": "adminy", "password": "long-enough-pw"})
    for path in ("/api/backup/workflows", "/api/backup/database"): assert a.get(path).status_code == 403
    assert a.post("/api/backup/restore", json={"format": "flowpilot-backup", "workflows": []}).status_code == 403
    assert a.put("/api/settings", json={"retention_days": 10, "max_executions": 10}).status_code == 200


# ---------------------------------------------------------------- tags
def test_tags_are_cleaned_listed_and_kept_when_omitted(client):
    body = {"name": "T", "description": "", "nodes": [node("t", "trigger")], "edges": [], "tags": [" Sales ", "sales", "ops-1", ""]}
    wid = client.post("/api/workflows", json=body).json()["id"]
    assert client.get(f"/api/workflows/{wid}").json()["tags"] == ["Sales", "ops-1"]
    assert next(w for w in client.get("/api/workflows").json() if w["id"] == wid)["tags"] == ["Sales", "ops-1"]
    client.put(f"/api/workflows/{wid}", json={"name": "T2", "description": "", "nodes": [node("t", "trigger")], "edges": []})  # no tags key
    assert client.get(f"/api/workflows/{wid}").json()["tags"] == ["Sales", "ops-1"]
    client.put(f"/api/workflows/{wid}", json={**body, "tags": []})
    assert client.get(f"/api/workflows/{wid}").json()["tags"] == []


@pytest.mark.parametrize("tags", [["<script>"], ["x" * 25], [f"t{i}" for i in range(11)]])
def test_bad_tags_are_rejected(client, tags):
    assert client.post("/api/workflows", json={"name": "T", "description": "", "nodes": [], "edges": [], "tags": tags}).status_code == 422


def test_duplicate_keeps_tags(client):
    wid = client.post("/api/workflows", json={"name": "T", "description": "", "nodes": [node("t", "trigger")], "edges": [], "tags": ["a"]}).json()["id"]
    copy = client.post(f"/api/workflows/{wid}/duplicate").json()["id"]
    assert client.get(f"/api/workflows/{copy}").json()["tags"] == ["a"]


# ---------------------------------------------------------------- sticky notes
def test_sticky_notes_are_ignored_by_the_engine(client):
    nodes = [node("t", "trigger"), node("s", "sticky", text="remember"), node("o", "output")]
    res = run(client, nodes, [edge("t", "o")])
    assert res["status"] == "success" and [s["node_id"] for s in res["steps"]] == ["t", "o"]


# ---------------------------------------------------------------- AI node
AI = "http://127.0.0.1:11434/v1?model=llama3.2"


def ai_flow(client, **data):
    nodes = [node("t", "trigger"), node("i", "input", json='{"name": "Ada"}'), node("a", "ai", credential="LLM", **data), node("o", "output")]
    return run(client, nodes, [edge("t", "i"), edge("i", "a"), edge("a", "o")])


def reply(text):
    return FakeResponse(200, {"choices": [{"message": {"content": text}}]})


def test_ai_node_calls_an_openai_compatible_endpoint(client, fake_http, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")
    client.put("/api/credentials/LLM", json={"value": AI})
    fake_http.reply["response"] = reply("Hello Ada!")
    res = ai_flow(client, prompt="Greet {{name}}", system="Be brief", temperature="0.5")
    assert res["status"] == "success" and res["output"] == {"text": "Hello Ada!", "model": "llama3.2"}
    call = fake_http.calls[-1]
    assert call["url"] == "http://127.0.0.1:11434/v1/chat/completions" and "Authorization" not in call["headers"]
    assert call["json"]["messages"] == [{"role": "system", "content": "Be brief"}, {"role": "user", "content": "Greet Ada"}]
    assert call["json"]["model"] == "llama3.2" and call["json"]["temperature"] == 0.5 and call["json"]["stream"] is False


def test_ai_json_output_and_key(client, fake_http, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")
    client.put("/api/credentials/LLM", json={"value": "http://127.0.0.1:1234/v1?model=m&key=sk-local-12345"})
    fake_http.reply["response"] = reply('```json\n{"sentiment": "good"}\n```')
    res = ai_flow(client, prompt="x", json_output="true")
    assert res["output"] == {"sentiment": "good"}
    assert fake_http.calls[-1]["headers"]["Authorization"] == "Bearer sk-local-12345"
    fake_http.reply["response"] = reply("not json")
    assert ai_flow(client, prompt="x", json_output="true")["status"] == "failed"


def test_ai_http_needs_the_private_hosts_switch(client, fake_http, monkeypatch):
    monkeypatch.delenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", raising=False)
    client.put("/api/credentials/LLM", json={"value": AI})
    res = ai_flow(client, prompt="x")
    assert res["status"] == "failed" and "FLOWPILOT_ALLOW_PRIVATE_HOSTS" in res["error"]
    assert fake_http.calls == []


@pytest.mark.parametrize("secret", ["ftp://x/v1?model=a", "https://api.example.com/v1", "https://api.example.com/v1?model=bad model!", "not a url"])
def test_ai_credential_shape_is_validated(client, fake_http, secret):
    client.put("/api/credentials/LLM", json={"value": secret})
    assert ai_flow(client, prompt="x")["status"] == "failed"


def test_ai_validation_and_odd_replies(client, fake_http, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")
    client.put("/api/credentials/LLM", json={"value": AI})
    assert ai_flow(client, prompt="   ")["status"] == "failed"
    assert ai_flow(client, prompt="x", temperature="hot")["status"] == "failed"
    fake_http.reply["response"] = FakeResponse(200, {"unexpected": True})
    assert "unexpected reply" in ai_flow(client, prompt="x")["error"]


# ---------------------------------------------------------------- webhook forms
FORM = json.dumps([{"name": "email", "label": "Your <email>", "type": "email"}, {"name": "note", "label": "Note", "type": "textarea"}, {"name": "bad name", "label": "x"}])


def form_hook(client):
    nodes = [node("w", "webhook", form_fields=FORM), node("m", "template", template="{{body.email}} / {{body.note}}"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("w", "m"), edge("m", "o")], name="Contact <b>us</b>")
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    return client.get(f"/api/workflows/{wid}").json()["webhook_token"], wid


def test_get_shows_an_escaped_form_with_safe_headers(client, anon):
    token, _ = form_hook(client)
    res = anon.get(f"/api/hooks/{token}")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/html")
    assert "&lt;email&gt;" in res.text and "Contact &lt;b&gt;us&lt;/b&gt;" in res.text and "<b>us</b>" not in res.text
    assert "name='email'" in res.text and "<textarea" in res.text and "bad name" not in res.text
    assert "default-src 'none'" in res.headers["content-security-policy"] and res.headers["x-content-type-options"] == "nosniff"


def test_form_post_runs_the_workflow_and_shows_a_thank_you(client, anon):
    token, wid = form_hook(client)
    res = anon.post(f"/api/hooks/{token}", data={"email": "a@example.com", "note": "hi", "extra": "ignored"}, headers={"accept": "text/html"})
    assert res.status_code == 200 and "Thank you" in res.text
    run_row = client.get("/api/executions", params={"workflow_id": wid}).json()[0]
    assert run_row["output"] == "a@example.com / hi"
    assert "extra" not in json.dumps(run_row["steps"])


def test_form_post_without_html_accept_returns_json(client, anon):
    token, _ = form_hook(client)
    res = anon.post(f"/api/hooks/{token}", data={"email": "a@example.com"})
    assert res.json()["status"] == "success"


def test_webhook_without_a_form_still_runs_on_get(client, anon):
    wid = make_flow(client, [node("w", "webhook"), node("o", "output")], [edge("w", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    assert anon.get(f"/api/hooks/{token}").json()["status"] == "success"


def test_broken_form_definition_means_no_form(client, anon):
    wid = make_flow(client, [node("w", "webhook", form_fields="{not json"), node("o", "output")], [edge("w", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    assert anon.get(f"/api/hooks/{token}").json()["status"] == "success"


def test_new_types_are_accepted(client):
    for t in ("sticky", "ai"):
        make_flow(client, [node("t", "trigger"), node("x", t)], [edge("t", "x")])
