import copy
import json

import pytest
from fastapi.testclient import TestClient

from app import connectors, main
from conftest import FakeResponse, edge, make_flow, node

MANIFEST = {
    "label": "Acme CRM", "base_url": "https://api.acme.example/v1", "auth": {"type": "bearer"},
    "operations": {
        "get_contact": {"label": "Get contact", "method": "GET", "path": "/contacts/{id}", "query": {"expand": "{expand}", "limit": 5}, "required": ["id"], "result": "data"},
        "create_contact": {"label": "Create contact", "method": "POST", "path": "/contacts", "body": {"name": "{name}", "age": "{age}", "tags": ["{tag}", "fixed"], "meta": {"note": "Hi {name}", "other": "{missing}"}}, "required": ["name"]},
    },
}


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db: db.execute("DELETE FROM connectors")


def add(client, name="acme", manifest=None):
    return client.put(f"/api/connectors/{name}", json={"manifest": manifest or MANIFEST})


def call(client, op, fields, connector="acme", secret="acme-secret-value-1", cred="ACMEKEY"):
    client.put(f"/api/credentials/{cred}", json={"value": secret})
    n = node("a", "app", service="connector", connector=connector, operation=op, credential=cred, fields=json.dumps(fields))
    wid = make_flow(client, [node("t", "trigger"), n, node("o", "output")], [edge("t", "a"), edge("a", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


def test_a_connector_can_be_saved_listed_read_and_deleted(client):
    res = add(client)
    assert res.status_code == 200 and res.json()["label"] == "Acme CRM" and set(res.json()["operations"]) == {"get_contact", "create_contact"}
    assert [c["name"] for c in client.get("/api/connectors").json()] == ["acme"]
    assert client.get("/api/connectors/acme").json()["base_url"] == "https://api.acme.example/v1"
    assert client.delete("/api/connectors/acme").status_code == 204
    assert client.get("/api/connectors").json() == [] and client.get("/api/connectors/acme").status_code == 404


def test_get_with_path_query_and_result_path(client, fake_http):
    add(client)
    fake_http.reply["response"] = FakeResponse(200, {"data": {"id": "a/b", "name": "Ann"}, "meta": 1})
    res = call(client, "get_contact", {"id": "a/b?x", "expand": "company"})
    assert res["status"] == "success" and res["output"] == {"id": "a/b", "name": "Ann"}
    c = fake_http.calls[-1]
    assert c["method"] == "GET" and c["url"] == "https://api.acme.example/v1/contacts/a%2Fb%3Fx" and c["params"] == {"expand": "company", "limit": 5}
    assert c["headers"]["Authorization"] == "Bearer acme-secret-value-1" and "acme-secret-value-1" not in json.dumps(res)
    call(client, "get_contact", {"id": "7"})
    assert fake_http.calls[-1]["params"] == {"limit": 5}  # the optional expand is dropped


def test_body_keeps_types_and_drops_missing_values(client, fake_http):
    add(client)
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    assert call(client, "create_contact", {"name": "Ann", "age": 30, "tag": "vip"})["status"] == "success"
    assert fake_http.calls[-1]["json"] == {"name": "Ann", "age": 30, "tags": ["vip", "fixed"], "meta": {"note": "Hi Ann"}}
    call(client, "create_contact", {"name": "Bob"})
    assert fake_http.calls[-1]["json"] == {"name": "Bob", "tags": ["fixed"], "meta": {"note": "Hi Bob"}}


@pytest.mark.parametrize("auth,check", [
    ({"type": "basic"}, lambda c: c["headers"]["Authorization"].startswith("Basic ")),
    ({"type": "header", "name": "X-Api-Key"}, lambda c: c["headers"]["X-Api-Key"] == "acme-secret-value-1"),
    ({"type": "query", "name": "key"}, lambda c: c["params"]["key"] == "acme-secret-value-1"),
])
def test_other_authentication_styles(client, fake_http, auth, check):
    add(client, manifest={**MANIFEST, "auth": auth})
    fake_http.reply["response"] = FakeResponse(200, {"data": 1})
    assert call(client, "get_contact", {"id": "1"})["status"] == "success" and check(fake_http.calls[-1])


def test_no_authentication_needs_no_credential(client, fake_http):
    add(client, manifest={**MANIFEST, "auth": {"type": "none"}})
    fake_http.reply["response"] = FakeResponse(200, {"data": 1})
    n = node("a", "app", service="connector", connector="acme", operation="get_contact", fields='{"id": "1"}')
    wid = make_flow(client, [node("t", "trigger"), n, node("o", "output")], [edge("t", "a"), edge("a", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success" and "Authorization" not in fake_http.calls[-1]["headers"]


def test_errors_are_failed_runs(client, fake_http):
    add(client)
    for op, fields, conn_name in (("get_contact", {}, "acme"), ("nope", {"id": "1"}, "acme"), ("get_contact", {"id": "1"}, "missing"), ("get_contact", {"id": "1"}, "")):
        res = call(client, op, fields, connector=conn_name)
        assert res["status"] == "failed" and res["error"], (op, conn_name)
    fake_http.reply["response"] = FakeResponse(500, {"error": "boom"})
    res = call(client, "get_contact", {"id": "1"}, secret="leaky-secret-value-9")
    assert res["status"] == "failed" and "leaky-secret-value-9" not in json.dumps(res)


def test_private_addresses_are_refused_at_call_time(client):
    manifest = copy.deepcopy(MANIFEST)
    manifest["base_url"] = "https://localhost/v1"
    add(client, manifest=manifest)
    res = call(client, "get_contact", {"id": "1"})  # no fake_http: the real address check runs
    assert res["status"] == "failed"


@pytest.mark.parametrize("change", [
    {"label": ""}, {"base_url": "http://api.example.com"}, {"base_url": "https://user:pw@api.example.com"}, {"base_url": "https://api.example.com:8443"},
    {"base_url": "https://api.example.com/a/../b"}, {"base_url": "https://api.example.com?x=1"}, {"auth": {"type": "magic"}}, {"auth": {"type": "header", "name": "Host"}},
    {"auth": {"type": "header"}}, {"auth": {"type": "query", "name": "a b"}}, {"operations": {}}, {"operations": []},
    {"operations": {"Bad Name": {"path": "/x"}}}, {"operations": {"x": {"path": "x"}}}, {"operations": {"x": {"path": "/a/../b"}}}, {"operations": {"x": {"path": "/a?b=1"}}},
    {"operations": {"x": {"path": "/a/{id"}}}, {"operations": {"x": {"path": "/a", "method": "TRACE"}}}, {"operations": {"x": {"path": "/a", "body": {"a": 1}}}},
    {"operations": {"x": {"path": "/a", "query": {"a": {"b": 1}}}}}, {"operations": {"x": {"path": "/a", "required": "id"}}}, {"operations": {"x": {"path": "/a", "result": "a b"}}},
])
def test_bad_manifests_are_rejected(client, change):
    bad = {**copy.deepcopy(MANIFEST), **change}
    body = json.dumps({"manifest": bad})
    assert client.put("/api/connectors/acme", content=body, headers={"Content-Type": "application/json"}).status_code == 400
    assert client.get("/api/connectors").json() == []


def test_bad_names_and_the_limit(client):
    for name in ("Acme", "1acme", "a" * 41, "a b"):
        assert client.put(f"/api/connectors/{name}", json={"manifest": MANIFEST}).status_code in (400, 404, 405, 422)
    for i in range(main.MAX_CONNECTORS): assert add(client, f"c{i}").status_code == 200
    assert add(client, "one-more").status_code == 400
    assert add(client, "c0").status_code == 200  # replacing is allowed


def test_only_owners_and_admins_change_connectors(client):
    add(client)
    made = {}
    for role in ("editor", "admin"):
        client.post("/api/users", json={"username": f"con-{role}", "password": "long-enough-pw", "role": role})
        c = TestClient(main.app)
        assert c.post("/api/auth/login", json={"username": f"con-{role}", "password": "long-enough-pw"}).status_code == 200
        made[role] = c
    assert made["editor"].get("/api/connectors").status_code == 200
    assert made["editor"].put("/api/connectors/acme", json={"manifest": MANIFEST}).status_code == 403 and made["editor"].delete("/api/connectors/acme").status_code == 403
    assert made["admin"].put("/api/connectors/acme", json={"manifest": MANIFEST}).status_code == 200
    users = client.get("/api/users").json()
    for role in made: client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'con-' + role)}")


def test_the_library_fills_and_quotes_values():
    m = connectors.validate_manifest(MANIFEST)
    req = connectors.build_request(m, "get_contact", {"id": "x y", "expand": True})
    assert req["url"].endswith("/contacts/x%20y") and req["params"] == {"expand": "true", "limit": 5}
    with pytest.raises(ValueError): connectors.build_request(m, "get_contact", {"id": ""})


def test_a_key_sent_in_the_address_never_shows_in_a_failed_run(client, fake_http):
    add(client, manifest={**MANIFEST, "auth": {"type": "query", "name": "key"}})
    fake_http.reply["response"] = FakeResponse(500, {"error": "boom"})
    res = call(client, "get_contact", {"id": "1"}, secret="query-key-secret-77")
    stored = client.get("/api/executions").json()
    assert res["status"] == "failed" and "query-key-secret-77" not in json.dumps(res) and "query-key-secret-77" not in json.dumps(stored)
