import json
from datetime import datetime, timedelta, timezone

import pytest

from app import main
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM poll_seen")
        db.execute("DELETE FROM poll_state")


URL = "https://api.example.com/items"


def poll_flow(client, **cfg):
    data = {"source": "json_list", "url": URL, "id_path": "id", "minutes": "5", **cfg}
    wid = make_flow(client, [node("p", "poll_in", **data), node("o", "output")], [edge("p", "o")])
    return wid


def activate(client, wid):
    res = client.post(f"/api/workflows/{wid}/active", json={"active": True})
    assert res.status_code == 200, res.text


def tick(wid):
    with main.conn() as db: db.execute("UPDATE workflows SET next_run_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), wid))
    return main.scheduler_tick()


def runs(wid):
    with main.conn() as db: return [dict(r) for r in db.execute("SELECT * FROM executions WHERE workflow_id=? AND trigger_kind='poll_in' ORDER BY started_at", (wid,)).fetchall()]


def items(*ids):
    return FakeResponse(200, [{"id": i, "title": f"item {i}"} for i in ids])


def test_first_check_records_what_exists_and_runs_nothing(client, fake_http):
    fake_http.reply["response"] = items(1, 2, 3)
    wid = poll_flow(client)
    activate(client, wid)
    assert tick(wid) == 1
    assert runs(wid) == []
    with main.conn() as db: assert db.execute("SELECT COUNT(*) c FROM poll_seen WHERE workflow_id=?", (wid,)).fetchone()["c"] == 3


def test_new_items_start_one_run_each_and_are_never_repeated(client, fake_http):
    fake_http.reply["response"] = items(1, 2)
    wid = poll_flow(client)
    activate(client, wid)
    tick(wid)
    fake_http.reply["response"] = items(1, 2, 3, 4)
    tick(wid)
    got = runs(wid)
    assert len(got) == 2 and all(r["status"] == "success" for r in got)
    assert {json.loads(r["output"])["id"] for r in got} == {3, 4}
    tick(wid)
    assert len(runs(wid)) == 2  # nothing new, nothing run


def test_newest_first_lists_are_dispatched_oldest_first(client, fake_http):
    fake_http.reply["response"] = items(2, 1)  # an API listing newest first
    wid = poll_flow(client)
    activate(client, wid)
    tick(wid)
    fake_http.reply["response"] = items(5, 4, 3, 2, 1)
    tick(wid)
    assert [json.loads(r["output"])["id"] for r in runs(wid)] == [3, 4, 5]


def test_oldest_first_option(client, fake_http):
    fake_http.reply["response"] = items(1)
    wid = poll_flow(client, order="oldest_first")
    activate(client, wid)
    tick(wid)
    fake_http.reply["response"] = items(1, 2, 3)
    tick(wid)
    assert [json.loads(r["output"])["id"] for r in runs(wid)] == [2, 3]


def test_a_burst_is_capped_per_check_and_the_rest_follow(client, fake_http):
    fake_http.reply["response"] = items(0)
    wid = poll_flow(client, order="oldest_first")
    activate(client, wid)
    tick(wid)
    fake_http.reply["response"] = items(*range(0, 26))
    tick(wid)
    assert len(runs(wid)) == main.MAX_POLL_ITEMS
    tick(wid)
    tick(wid)
    assert len(runs(wid)) == 25 and {json.loads(r["output"])["id"] for r in runs(wid)} == set(range(1, 26))


def test_items_path_and_whole_item_fingerprint(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"data": {"rows": [{"a": 1}]}})
    wid = poll_flow(client, items_path="data.rows", id_path="")
    activate(client, wid)
    tick(wid)
    fake_http.reply["response"] = FakeResponse(200, {"data": {"rows": [{"a": 1}, {"a": 2}]}})
    tick(wid)
    assert [json.loads(r["output"]) for r in runs(wid)] == [{"a": 2}]
    fake_http.reply["response"] = FakeResponse(200, {"data": {"rows": [{"a": 1}, {"a": 2}, {"a": 2}]}})
    tick(wid)
    assert len(runs(wid)) == 1


def test_not_a_list_is_a_visible_failed_run(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"oops": True})
    wid = poll_flow(client)
    activate(client, wid)
    tick(wid)
    got = runs(wid)
    assert len(got) == 1 and got[0]["status"] == "failed" and "did not return a list" in got[0]["error"]


def test_http_errors_do_not_leak_the_credential(client, fake_http):
    client.put("/api/credentials/POLLTOK", json={"value": "secret-poll-token-value"})
    fake_http.reply["response"] = FakeResponse(500, {"error": "down"})
    wid = poll_flow(client, credential="POLLTOK")
    activate(client, wid)
    tick(wid)
    got = runs(wid)
    assert got and got[0]["status"] == "failed" and "secret-poll-token-value" not in json.dumps(got)
    assert fake_http.calls[-1]["headers"]["Authorization"] == "Bearer secret-poll-token-value"


RSS = """<rss><channel>{}</channel></rss>"""
ITEM = "<item><title>{t}</title><link>https://example.com/{t}</link><description>d {t}</description></item>"


def feed(*names):
    return FakeResponse(200, None, text=RSS.format("".join(ITEM.format(t=n) for n in names)))


def test_rss_new_posts(client, fake_http):
    fake_http.reply["response"] = feed("b", "a")  # newest first, as feeds are
    wid = make_flow(client, [node("p", "poll_in", source="rss", url="https://example.com/feed.xml", minutes="5"), node("o", "output")], [edge("p", "o")])
    activate(client, wid)
    tick(wid)
    assert runs(wid) == []
    fake_http.reply["response"] = feed("d", "c", "b", "a")
    tick(wid)
    got = [json.loads(r["output"]) for r in runs(wid)]
    assert [g["title"] for g in got] == ["c", "d"] and got[0]["link"] == "https://example.com/c"


def issue(n, pr=False):
    d = {"number": n, "title": f"Bug {n}", "state": "open", "html_url": f"https://github.com/o/r/issues/{n}", "user": {"login": "ada"}, "body": "text", "created_at": "2026-01-01T00:00:00Z", "labels": [{"name": "bug"}]}
    if pr: d["pull_request"] = {}
    return d


def test_github_issues_skip_pull_requests(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, [issue(2)])
    wid = make_flow(client, [node("p", "poll_in", source="github_issues", repo="o/r", minutes="5"), node("o", "output")], [edge("p", "o")])
    activate(client, wid)
    tick(wid)
    fake_http.reply["response"] = FakeResponse(200, [issue(5, pr=True), issue(4), issue(3), issue(2)])
    tick(wid)
    got = [json.loads(r["output"]) for r in runs(wid)]
    assert [g["number"] for g in got] == [3, 4] and got[0]["user"] == "ada" and got[0]["labels"] == ["bug"]
    assert "api.github.com/repos/o/r/issues" in fake_http.calls[-1]["url"]


def test_reactivating_starts_from_a_fresh_baseline(client, fake_http):
    fake_http.reply["response"] = items(1)
    wid = poll_flow(client)
    activate(client, wid)
    tick(wid)
    client.post(f"/api/workflows/{wid}/active", json={"active": False})
    fake_http.reply["response"] = items(1, 2, 3)  # appeared while switched off
    activate(client, wid)
    tick(wid)
    assert runs(wid) == []  # not a flood of old items
    fake_http.reply["response"] = items(1, 2, 3, 4)
    tick(wid)
    assert len(runs(wid)) == 1


def test_activation_validates_the_settings(client):
    for cfg in ({"source": "nope"}, {"url": "http://insecure.example.com"}, {"url": ""}, {"minutes": "0"}, {"minutes": "abc"}, {"order": "sideways"}):
        wid = poll_flow(client, **cfg)
        assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 400, cfg
    wid = make_flow(client, [node("p", "poll_in", source="github_issues", repo="not a repo"), node("o", "output")], [edge("p", "o")])
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 400


def test_manual_run_uses_a_sample_item_and_list_shows_the_trigger(client):
    wid = poll_flow(client)
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success" and res["output"]["title"] == "Sample item"
    listed = next(w for w in client.get("/api/workflows").json() if w["id"] == wid)
    assert listed["trigger"] == "poll_in"


def test_deleting_a_workflow_forgets_its_seen_items(client, fake_http):
    fake_http.reply["response"] = items(1, 2)
    wid = poll_flow(client)
    activate(client, wid)
    tick(wid)
    client.delete(f"/api/workflows/{wid}")
    with main.conn() as db:
        assert db.execute("SELECT COUNT(*) c FROM poll_seen WHERE workflow_id=?", (wid,)).fetchone()["c"] == 0
        assert db.execute("SELECT COUNT(*) c FROM poll_state WHERE workflow_id=?", (wid,)).fetchone()["c"] == 0


def test_seen_list_is_trimmed(client, fake_http, monkeypatch):
    monkeypatch.setattr(main, "POLL_SEEN_KEEP", 5)
    fake_http.reply["response"] = items(*range(10))
    wid = poll_flow(client)
    activate(client, wid)
    tick(wid)
    with main.conn() as db: assert db.execute("SELECT COUNT(*) c FROM poll_seen WHERE workflow_id=?", (wid,)).fetchone()["c"] == 5


def test_a_second_trigger_cannot_be_combined(client):
    nodes = [node("p", "poll_in", source="json_list", url=URL), node("t", "trigger"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("p", "o"), edge("t", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "failed"
