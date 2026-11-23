import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import main
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _env(clean_state):
    with main.conn() as db: db.execute("DELETE FROM executions")
    yield
    with main.conn() as db:
        db.execute("DELETE FROM executions")
        db.execute("DELETE FROM connectors")


def flow(client):
    return make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="Filter flow")


def add_runs(wid, count, start, tags=None, note=None):
    with main.conn() as db:
        for i in range(count):
            s = start + timedelta(seconds=i)
            db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind,tags,note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), wid, "success", s.isoformat(), s.isoformat(), "null", "[]", None, "manual", json.dumps(tags) if tags else None, note))


def test_a_tag_filter_finds_runs_older_than_the_newest_hundred(client):
    wid = flow(client)
    base = datetime.now(timezone.utc) - timedelta(days=2)
    add_runs(wid, 3, base, tags=["refund"])
    add_runs(wid, 150, base + timedelta(hours=1))
    assert len(client.get("/api/executions", params={"workflow_id": wid}).json()) == 100
    got = client.get("/api/executions", params={"workflow_id": wid, "tag": "refund"}).json()
    assert len(got) == 3 and all(r["tags"] == ["refund"] for r in got)


def test_the_tag_is_matched_the_way_tags_are_stored(client):
    wid = flow(client)
    add_runs(wid, 2, datetime.now(timezone.utc), tags=["needs-review"])
    assert len(client.get("/api/executions", params={"tag": "Needs Review"}).json()) == 2
    assert client.get("/api/executions", params={"tag": "other"}).json() == []


def test_the_noted_filter_keeps_only_runs_with_a_note(client):
    wid = flow(client)
    base = datetime.now(timezone.utc) - timedelta(days=1)
    add_runs(wid, 1, base, note="customer confirmed fixed")
    add_runs(wid, 120, base + timedelta(hours=1))
    got = client.get("/api/executions", params={"workflow_id": wid, "noted": "true"}).json()
    assert len(got) == 1 and got[0]["note"] == "customer confirmed fixed"


def test_tag_and_noted_combine(client):
    wid = flow(client)
    base = datetime.now(timezone.utc)
    add_runs(wid, 1, base, tags=["a"], note="x")
    add_runs(wid, 2, base + timedelta(minutes=1), tags=["a"])
    assert len(client.get("/api/executions", params={"tag": "a"}).json()) == 3
    assert len(client.get("/api/executions", params={"tag": "a", "noted": "true"}).json()) == 1


def test_no_filter_still_returns_the_newest_hundred(client):
    wid = flow(client)
    add_runs(wid, 130, datetime.now(timezone.utc))
    assert len(client.get("/api/executions", params={"workflow_id": wid}).json()) == 100


# --- live check of a connector ---------------------------------------------------
def save(client, key="frankfurter"):
    pick = next(e for e in client.get("/api/connector-library").json() if e["id"] == key)
    assert client.put(f"/api/connectors/{key}", json={"manifest": pick["manifest"]}).status_code == 200
    return pick


def test_a_read_operation_can_be_checked(client, fake_http):
    save(client)
    fake_http.reply["response"] = FakeResponse(200, {"amount": 1, "base": "EUR", "rates": {"USD": 1.1}})
    res = client.post("/api/connectors/frankfurter/check", json={"operation": "latest", "fields": {"from": "EUR", "to": "USD"}}).json()
    assert res["ok"] is True and res["error"] is None and "USD" in res["sample"] and res["seconds"] >= 0
    assert fake_http.calls[-1]["url"] == "https://api.frankfurter.app/latest"


def test_a_check_uses_the_saved_key_but_never_returns_it(client, fake_http):
    save(client, "hunter")
    client.put("/api/credentials/HUNTERKEY", json={"value": "super-secret-hunter-key"})
    fake_http.reply["response"] = FakeResponse(200, {"data": {"domain": "stripe.com", "emails": []}})
    res = client.post("/api/connectors/hunter/check", json={"operation": "domain_search", "fields": {"domain": "stripe.com"}, "credential": "HUNTERKEY"})
    assert res.json()["ok"] is True, res.text
    assert fake_http.calls[-1]["params"]["api_key"] == "super-secret-hunter-key"
    assert "super-secret-hunter-key" not in res.text


def test_a_failing_service_gives_ok_false_and_the_reason(client, fake_http):
    save(client)
    fake_http.reply["response"] = FakeResponse(401, {"message": "bad key"}, text="bad key")
    res = client.post("/api/connectors/frankfurter/check", json={"operation": "latest", "fields": {}})
    assert res.status_code == 200 and res.json()["ok"] is False and res.json()["error"] and res.json()["sample"] is None


def test_an_operation_that_changes_data_is_refused(client, fake_http):
    save(client, "postmark")
    res = client.post("/api/connectors/postmark/check", json={"operation": "send", "fields": {}, "credential": "X"})
    assert res.status_code == 400 and "read" in res.json()["detail"]
    assert fake_http.calls == []


def test_unknown_connector_or_operation(client, fake_http):
    assert client.post("/api/connectors/nope/check", json={"operation": "x"}).status_code == 404
    save(client)
    assert client.post("/api/connectors/frankfurter/check", json={"operation": "nope"}).status_code == 400


def test_a_long_reply_is_cut_short(client, fake_http):
    save(client)
    fake_http.reply["response"] = FakeResponse(200, {"rows": ["x" * 50] * 100})
    res = client.post("/api/connectors/frankfurter/check", json={"operation": "latest", "fields": {}}).json()
    assert res["ok"] and len(res["sample"]) <= 403 and res["sample"].endswith("...")


def test_only_admins_may_check(client):
    from app.main import allowed
    assert allowed("admin", "POST", "/api/connectors/x/check") and allowed("owner", "POST", "/api/connectors/x/check")
    assert not allowed("editor", "POST", "/api/connectors/x/check") and not allowed("viewer", "POST", "/api/connectors/x/check")
