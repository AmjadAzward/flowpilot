import json
import time

import pytest

from app import main
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


PEOPLE = json.dumps([{"name": "Cy", "age": 30, "city": "Oslo"}, {"name": "ada", "age": 25, "city": "Rome"}, {"name": "Bo", "age": 30, "city": "Oslo"}, {"name": "Di", "age": None, "city": "Rome"}])


def tool(client, typ, data_json=PEOPLE, **cfg):
    nodes = [node("t", "trigger"), node("i", "input", json=data_json), node("x", typ, **cfg), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "x"), edge("x", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


# ---------------------------------------------------------------- item tools
def test_sort_by_number_text_and_direction(client):
    ages = [p["name"] for p in tool(client, "sort", path="age", order="asc")["output"]]
    assert ages == ["ada", "Cy", "Bo", "Di"]  # numbers first, empty last, stable for ties
    assert [p["name"] for p in tool(client, "sort", path="age", order="desc")["output"]] == ["Di", "Cy", "Bo", "ada"]
    assert [p["name"] for p in tool(client, "sort", path="name")["output"]] == ["ada", "Bo", "Cy", "Di"]  # case-insensitive text


def test_sort_plain_values(client):
    assert tool(client, "sort", data_json="[3, 1, 2]", order="desc")["output"] == [3, 2, 1]
    assert tool(client, "sort", data_json='{"a": 1}')["status"] == "failed"


def test_limit_first_and_last(client):
    assert [p["name"] for p in tool(client, "limit", count="2")["output"]] == ["Cy", "ada"]
    assert [p["name"] for p in tool(client, "limit", count="2", **{"from": "last"})["output"]] == ["Bo", "Di"]
    assert tool(client, "limit", count="0")["output"] == []
    assert tool(client, "limit", count="abc")["status"] == "failed"


def test_remove_duplicates_by_field_or_whole_item(client):
    assert [p["name"] for p in tool(client, "dedupe", path="city")["output"]] == ["Cy", "ada"]
    assert tool(client, "dedupe", data_json="[1, 1, 2, 1, 3, 2]")["output"] == [1, 2, 3]
    assert tool(client, "dedupe", data_json='[{"a": 1, "b": 2}, {"b": 2, "a": 1}]')["output"] == [{"a": 1, "b": 2}]


def test_split_out_a_list_inside_an_object(client):
    assert tool(client, "split", data_json='{"data": {"rows": [1, 2, 3]}}', path="data.rows")["output"] == [1, 2, 3]
    assert tool(client, "split", data_json='{"data": 5}', path="data")["status"] == "failed"


@pytest.mark.parametrize("op,expected", [("count", 4), ("sum", 85), ("avg", 28.333333333), ("min", 25), ("max", 30)])
def test_summarize_numbers(client, op, expected):
    out = tool(client, "aggregate", path="age", operation=op)["output"]
    assert out == pytest.approx(expected)


def test_summarize_collect_and_errors(client):
    assert tool(client, "aggregate", path="city", operation="collect")["output"] == ["Oslo", "Rome", "Oslo", "Rome"]
    res = tool(client, "aggregate", path="name", operation="sum")
    assert res["status"] == "failed" and "not a number" in res["error"]
    assert tool(client, "aggregate", data_json="[]", operation="sum")["output"] == 0
    assert tool(client, "aggregate", data_json="[]", operation="avg")["output"] is None


def test_rename_keys_on_objects_and_lists(client):
    assert tool(client, "rename", data_json='{"a": 1, "b": 2}', mapping='{"a": "first"}')["output"] == {"first": 1, "b": 2}
    assert tool(client, "rename", data_json='[{"a": 1}, {"a": 2}]', mapping='{"a": "x"}')["output"] == [{"x": 1}, {"x": 2}]
    assert tool(client, "rename", mapping='{"a": 5}')["status"] == "failed"


def test_datetime_add_subtract_diff_format(client):
    assert tool(client, "datetime", data_json="{}", operation="add", value="2025-01-31", amount="1", unit="days")["output"] == "2025-02-01T00:00:00+00:00"
    assert tool(client, "datetime", data_json="{}", operation="subtract", value="2025-01-01T10:00:00Z", amount="2", unit="hours")["output"] == "2025-01-01T08:00:00+00:00"
    assert tool(client, "datetime", data_json="{}", operation="diff", value="2025-01-01", other="2025-01-11", unit="days")["output"] == 10
    assert tool(client, "datetime", data_json='{"d": "2025-03-05"}', operation="format", value="{{d}}", format="%d/%m/%Y")["output"] == "05/03/2025"
    assert tool(client, "datetime", data_json="{}", operation="format", value="0", format="%Y")["output"] == "1970"
    assert "T" in tool(client, "datetime", data_json="{}", operation="now")["output"]


def test_datetime_errors(client):
    assert "Cannot read" in tool(client, "datetime", data_json="{}", operation="add", value="not a date", amount="1")["error"]
    assert tool(client, "datetime", data_json="{}", operation="add", value="2025-01-01", amount="x")["status"] == "failed"
    assert tool(client, "datetime", data_json="{}", operation="add", value="2025-01-01", amount="1", unit="years")["status"] == "failed"
    assert tool(client, "datetime", data_json="{}", operation="add", value="2025-01-01", amount="99999999999", unit="days")["status"] == "failed"


def test_datetime_per_item(client):
    out = tool(client, "datetime", data_json='[{"d": "2025-01-01"}, {"d": "2025-06-01"}]', each_item="true", operation="format", value="{{d}}", format="%m")["output"]
    assert out == ["01", "06"]


def test_crypto_hashes_and_encoding(client):
    assert tool(client, "crypto", data_json="{}", operation="sha256", value="abc")["output"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert tool(client, "crypto", data_json="{}", operation="md5", value="abc")["output"] == "900150983cd24fb0d6963f7d28e17f72"
    assert tool(client, "crypto", data_json="{}", operation="sha1", value="abc")["output"] == "a9993e364706816aba3e25717850c26c9cd0d89d"
    assert tool(client, "crypto", data_json="{}", operation="base64_encode", value="hi there")["output"] == "aGkgdGhlcmU="
    assert tool(client, "crypto", data_json="{}", operation="base64_decode", value="aGkgdGhlcmU=")["output"] == "hi there"
    assert tool(client, "crypto", data_json="{}", operation="base64_decode", value="%%%")["status"] == "failed"
    assert len(tool(client, "crypto", data_json="{}", operation="uuid")["output"]) == 36
    assert len(tool(client, "crypto", data_json="{}", operation="random", value="24")["output"]) == 24


def test_hmac_uses_a_credential_key_and_hides_it(client):
    client.put("/api/credentials/SIGKEY", json={"value": "super-secret-signing-key"})
    res = tool(client, "crypto", data_json="{}", operation="hmac_sha256", value="payload", key="{{cred.SIGKEY}}")
    import hashlib, hmac as hm
    assert res["output"] == hm.new(b"super-secret-signing-key", b"payload", hashlib.sha256).hexdigest()
    assert "super-secret-signing-key" not in json.dumps(res)
    assert tool(client, "crypto", data_json="{}", operation="hmac_sha256", value="x")["status"] == "failed"


def test_item_tools_can_be_pinned_and_retried_like_other_nodes(client):
    res = tool(client, "sort", path="age", pin='["pinned"]')
    assert res["output"] == ["pinned"]


def test_pipeline_of_item_tools(client):
    nodes = [node("t", "trigger"), node("i", "input", json=PEOPLE), node("d", "dedupe", path="city"), node("s", "sort", path="name"), node("n", "aggregate", operation="collect", path="name"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "d"), edge("d", "s"), edge("s", "n"), edge("n", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["output"] == ["ada", "Cy"]


# ---------------------------------------------------------------- per-workflow settings
def save_with(client, nodes, edges, settings):
    res = client.post("/api/workflows", json={"name": "s", "description": "", "nodes": nodes, "edges": edges, "settings": settings})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_settings_are_validated_and_stored(client):
    nodes, edges = [node("t", "trigger"), node("o", "output")], [edge("t", "o")]
    for bad in ({"timeout_seconds": 2}, {"timeout_seconds": 99999}, {"timeout_seconds": "abc"}, {"save_runs": "sometimes"}):
        assert client.post("/api/workflows", json={"name": "s", "description": "", "nodes": nodes, "edges": edges, "settings": bad}).status_code == 422
    wid = save_with(client, nodes, edges, {"timeout_seconds": 30, "save_runs": "failures", "junk": 1})
    assert client.get(f"/api/workflows/{wid}").json()["settings"] == {"timeout_seconds": 30, "save_runs": "failures"}


def test_saving_without_settings_keeps_them(client):
    nodes, edges = [node("t", "trigger"), node("o", "output")], [edge("t", "o")]
    wid = save_with(client, nodes, edges, {"timeout_seconds": 60})
    client.put(f"/api/workflows/{wid}", json={"name": "renamed", "description": "", "nodes": nodes, "edges": edges})
    assert client.get(f"/api/workflows/{wid}").json()["settings"] == {"timeout_seconds": 60}
    client.put(f"/api/workflows/{wid}", json={"name": "renamed", "description": "", "nodes": nodes, "edges": edges, "settings": {}})
    assert client.get(f"/api/workflows/{wid}").json()["settings"] == {}


def test_time_limit_stops_a_run_between_nodes(client, monkeypatch):
    orig = main.Graph.__init__

    def init(self, doc):
        orig(self, doc)
        if self.limit: self.deadline = time.monotonic() - 1  # pretend the limit has already passed

    monkeypatch.setattr(main.Graph, "__init__", init)
    nodes = [node("t", "trigger"), node("a", "input", json="1"), node("o", "output")]
    wid = save_with(client, nodes, [edge("t", "a"), edge("a", "o")], {"timeout_seconds": 5})
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "time limit of 5 seconds" in res["error"]
    wid2 = save_with(client, nodes, [edge("t", "a"), edge("a", "o")], {})
    assert client.post(f"/api/workflows/{wid2}/run").json()["status"] == "success"


def count_runs(wid):
    with main.conn() as db: return db.execute("SELECT COUNT(*) c FROM executions WHERE workflow_id=?", (wid,)).fetchone()["c"]


def webhook_flow(client, settings, fail=False):
    nodes = [node("w", "webhook"), node("f", "fail", message="boom") if fail else node("m", "note"), node("o", "output")]
    mid = nodes[1]["id"]
    wid = save_with(client, nodes, [edge("w", mid), edge(mid, "o")], settings)
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    return wid, client.get(f"/api/workflows/{wid}").json()["webhook_token"]


def test_save_failures_only_skips_successful_automatic_runs(client, anon):
    wid, token = webhook_flow(client, {"save_runs": "failures"})
    assert anon.post(f"/api/hooks/{token}", json={}).status_code in (200, 202)
    assert count_runs(wid) == 0
    client.post(f"/api/workflows/{wid}/run")  # a manual test run is always kept
    assert count_runs(wid) == 1


def test_save_failures_only_keeps_failed_runs(client, anon):
    wid, token = webhook_flow(client, {"save_runs": "failures"}, fail=True)
    anon.post(f"/api/hooks/{token}", json={})
    assert count_runs(wid) == 1


def test_save_none_skips_everything_automatic(client, anon):
    wid, token = webhook_flow(client, {"save_runs": "none"}, fail=True)
    anon.post(f"/api/hooks/{token}", json={})
    assert count_runs(wid) == 0


def test_default_keeps_every_run(client, anon):
    wid, token = webhook_flow(client, {})
    anon.post(f"/api/hooks/{token}", json={})
    assert count_runs(wid) == 1


# ---------------------------------------------------------------- replay from the failed node
def failing_flow(client, fake_http):
    fake_http.reply["response"] = FakeResponse(500, {"error": "down"})
    nodes = [node("t", "trigger"), node("i", "input", json='{"id": 7}'), node("s", "set", json='{"id": {{id}}, "tag": "x"}'),
             node("h", "http", url="https://api.example.com/items/{{id}}", method="GET"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "s"), edge("s", "h"), edge("h", "o")])
    return wid, client.post(f"/api/workflows/{wid}/run").json()


def test_replay_continues_from_the_failed_node(client, fake_http):
    wid, run = failing_flow(client, fake_http)
    assert run["status"] == "failed"
    fake_http.reply["response"] = FakeResponse(200, {"ok": True, "n": 1})
    before = len(fake_http.calls)
    res = client.post(f"/api/executions/{run['id']}/replay")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "success" and body["trigger"] == "replay" and body["replayed_from"] == run["id"] and body["id"] != run["id"]
    assert len(fake_http.calls) == before + 1 and fake_http.calls[-1]["url"].endswith("/items/7")  # only the failed node onward ran, with its original input
    assert [s["node_id"] for s in body["steps"]] == ["t", "i", "s", "h", "o"] and body["steps"][3]["status"] == "success"
    listed = {r["id"]: r for r in client.get("/api/executions").json()}
    assert listed[body["id"]]["trigger"] == "replay" and listed[run["id"]]["status"] == "failed"  # the original is untouched


def test_replay_can_fail_again_and_be_replayed_again(client, fake_http):
    wid, run = failing_flow(client, fake_http)
    second = client.post(f"/api/executions/{run['id']}/replay").json()
    assert second["status"] == "failed"
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    assert client.post(f"/api/executions/{second['id']}/replay").json()["status"] == "success"


def test_replay_refuses_runs_that_cannot_be_replayed(client, fake_http):
    ok = client.post(f"/api/workflows/{make_flow(client, [node('t', 'trigger'), node('o', 'output')], [edge('t', 'o')])}/run").json()
    assert client.post(f"/api/executions/{ok['id']}/replay").status_code == 400
    assert client.post("/api/executions/nope/replay").status_code == 404
    nodes = [node("t", "trigger"), node("i", "input", json='[1, 2]'), node("l", "loop", path=""), node("f", "fail", message="inside"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "l"), edge("l", "f", "each"), edge("l", "o", "done")])
    run = client.post(f"/api/workflows/{wid}/run").json()
    assert run["status"] == "failed"
    res = client.post(f"/api/executions/{run['id']}/replay")
    assert res.status_code == 400 and "loop" in res.json()["detail"]


def test_replay_refuses_when_the_node_was_deleted(client, fake_http):
    wid, run = failing_flow(client, fake_http)
    nodes = [node("t", "trigger"), node("o", "output")]
    client.put(f"/api/workflows/{wid}", json={"name": "t", "description": "", "nodes": nodes, "edges": [edge("t", "o")]})
    res = client.post(f"/api/executions/{run['id']}/replay").json()
    assert res["status"] == "failed" and "no longer exists" in res["error"]


def test_a_viewer_cannot_replay(client, anon):
    client.post("/api/users", json={"username": "viewer-b6", "password": "long-enough-pw", "role": "viewer"})
    from fastapi.testclient import TestClient
    v = TestClient(main.app)
    assert v.post("/api/auth/login", json={"username": "viewer-b6", "password": "long-enough-pw"}).status_code == 200
    assert v.post("/api/executions/anything/replay").status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'viewer-b6')}")


# ---------------------------------------------------------------- audit log
def test_audit_log_records_changes_but_not_reads(client):
    wid = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="audited")
    client.get("/api/workflows")
    client.put("/api/credentials/AUDITED_KEY", json={"value": "audit-secret-value"})
    rows = client.get("/api/audit").json()
    seen = {(r["method"], r["path"]) for r in rows}
    assert ("POST", "/api/workflows") in seen and ("PUT", "/api/credentials/AUDITED_KEY") in seen
    assert not any(r["method"] == "GET" for r in rows)
    assert "audit-secret-value" not in json.dumps(rows)
    mine = next(r for r in rows if r["path"] == "/api/credentials/AUDITED_KEY")
    assert mine["username"] and mine["role"] == "owner" and mine["status"] < 400 and mine["at"]
    client.delete(f"/api/workflows/{wid}")
    assert any(r["method"] == "DELETE" and r["path"] == f"/api/workflows/{wid}" for r in client.get("/api/audit").json())


def test_audit_log_skips_failed_requests_and_webhooks(client, anon):
    client.post("/api/workflows", json={"name": "", "nodes": [], "edges": []})  # rejected
    wid = make_flow(client, [node("w", "webhook"), node("o", "output")], [edge("w", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    anon.post(f"/api/hooks/{token}", json={})
    rows = client.get("/api/audit?limit=1000").json()
    assert not any(r["path"].startswith("/api/hooks/") for r in rows) and not any(r["status"] >= 400 and r["path"] != "/api/auth/login" for r in rows)


def test_audit_log_paging_filter_and_cap(client, monkeypatch):
    for _ in range(3): client.put("/api/credentials/PAGE_KEY", json={"value": "page-secret-value"})
    assert len(client.get("/api/audit?limit=2").json()) == 2
    assert client.get("/api/audit?limit=1&offset=1").json()[0]["id"] < client.get("/api/audit?limit=1").json()[0]["id"]
    assert client.get("/api/audit?who=nobody-here").json() == []
    monkeypatch.setattr(main, "AUDIT_KEEP", 5)
    for _ in range(8): client.put("/api/credentials/PAGE_KEY", json={"value": "page-secret-value"})
    with main.conn() as db: assert db.execute("SELECT COUNT(*) c FROM audit_log").fetchone()["c"] <= 6


def test_logins_are_recorded_by_name_and_failures_never_store_what_was_typed(client, anon):
    from fastapi.testclient import TestClient
    from conftest import OWNER
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json=OWNER).status_code == 200
    assert c.post("/api/auth/login", json={"username": OWNER["username"], "password": "wrong-password-typed"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "my-secret-password-typo", "password": "x"}).status_code == 401
    rows = [r for r in client.get("/api/audit?limit=20").json() if r["path"] == "/api/auth/login"]
    assert any(r["status"] == 200 and r["username"] == OWNER["username"].lower() for r in rows)
    assert any(r["status"] == 401 and r["username"] == OWNER["username"].lower() for r in rows)
    assert any(r["status"] == 401 and r["username"] == "(unknown user)" for r in rows)
    assert "wrong-password-typed" not in json.dumps(rows) and "my-secret-password-typo" not in json.dumps(rows)
