import base64
import json

import pytest
from cryptography.fernet import Fernet

from app import main
from conftest import FakeResponse, edge, make_flow, node

SECRET = "sk-live-abcdef123456"


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def put(client, name="API", value=SECRET):
    return client.put(f"/api/credentials/{name}", json={"value": value})


def run(client, nodes, edges, body=None):
    wid = make_flow(client, nodes, edges)
    res = client.post(f"/api/workflows/{wid}/run", json=body) if body else client.post(f"/api/workflows/{wid}/run")
    assert res.status_code == 200
    return res.json()


def http_flow(client, **cfg):
    return run(client, [node("t", "trigger"), node("h", "http", url="https://api.example.com/x", **cfg)], [edge("t", "h")])


# ---------------------------------------------------------------- API
def test_credentials_api_never_returns_values(client):
    assert client.get("/api/credentials").json() == []
    assert put(client).json() == {"name": "API", "saved": True}
    listing = client.get("/api/credentials")
    assert [c["name"] for c in listing.json()] == ["API"]
    assert SECRET not in listing.text and "value" not in listing.json()[0]


def test_updating_a_credential_keeps_one_row(client):
    put(client)
    put(client, value="a-different-secret")
    rows = client.get("/api/credentials").json()
    assert len(rows) == 1 and rows[0]["updated_at"] >= rows[0]["created_at"]


def test_delete_credential(client):
    put(client)
    assert client.delete("/api/credentials/API").status_code == 204
    assert client.get("/api/credentials").json() == []


@pytest.mark.parametrize("name", ["1abc", "has space", "x" * 41, "bad.name", "-lead"])
def test_credential_name_validation(client, name):
    assert put(client, name=name).status_code == 400


def test_credential_value_validation(client):
    assert put(client, value="abc").status_code == 422  # too short to hide reliably
    assert put(client, value="line1\nline2").status_code == 400
    assert put(client, value="x" * 4001).status_code == 422


def test_credential_count_is_limited(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_CREDENTIALS", 2)
    assert put(client, "A").status_code == 200 and put(client, "B").status_code == 200
    assert put(client, "C").status_code == 400
    assert put(client, "A", "replacement").status_code == 200  # updating is still fine


def test_credentials_require_login(anon, client):
    put(client)
    assert anon.get("/api/credentials").status_code == 401
    assert anon.put("/api/credentials/X", json={"value": "abcdef"}).status_code == 401
    assert anon.delete("/api/credentials/API").status_code == 401


# ---------------------------------------------------------------- encryption at rest
def test_values_are_encrypted_in_the_database(client):
    put(client)
    with main.conn() as db:
        stored = db.execute("SELECT value_enc FROM credentials WHERE name='API'").fetchone()["value_enc"]
    assert SECRET not in stored
    assert main.fernet().decrypt(stored.encode()).decode() == SECRET


def test_key_file_is_created_next_to_the_database(client):
    put(client)
    key_file = main.DB_PATH.parent / "flowpilot.key"
    assert key_file.exists() and len(key_file.read_bytes().strip()) == 44


def test_database_alone_cannot_decrypt(client, monkeypatch):
    put(client)
    monkeypatch.setitem(main._fernets, str(main.DB_PATH.parent / "flowpilot.key"), Fernet(Fernet.generate_key()))  # a different key
    assert main.load_secrets() == {"API": None}
    res = run(client, [node("t", "trigger"), node("m", "template", template="{{cred.API}}")], [edge("t", "m")])
    assert res["status"] == "failed" and "cannot be decrypted" in res["error"]


# ---------------------------------------------------------------- using credentials
def test_placeholder_resolves_but_the_result_is_redacted(client):
    put(client)
    res = run(client, [node("t", "trigger"), node("m", "template", template="key={{cred.API}}"), node("o", "output")], [edge("t", "m"), edge("m", "o")])
    assert res["status"] == "success" and res["output"] == "key=[hidden]"
    assert SECRET not in json.dumps(res)
    stored = client.get("/api/executions").json()[0]
    assert SECRET not in json.dumps(stored)


def test_unknown_credential_fails_clearly(client):
    res = run(client, [node("t", "trigger"), node("m", "template", template="{{cred.NOPE}}")], [edge("t", "m")])
    assert res["status"] == "failed" and res["error"] == "Unknown credential: NOPE"


def test_data_cannot_smuggle_in_a_credential_placeholder(client):
    put(client)
    nodes = [node("t", "trigger"), node("i", "input", json='{"x": "{{cred.API}}"}'), node("m", "template", template="{{x}}")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "m")])
    assert res["output"] == "{{cred.API}}"  # rendered once, so data is never re-interpreted


def test_credentials_work_inside_sub_workflows(client):
    put(client)
    child = make_flow(client, [node("t", "trigger"), node("m", "template", template="{{cred.API}}"), node("o", "output")], [edge("t", "m"), edge("m", "o")])
    res = run(client, [node("t", "trigger"), node("s", "subflow", workflow_id=child)], [edge("t", "s")])
    assert res["status"] == "success" and res["output"] == "[hidden]"


def test_redaction_handles_json_escaped_secrets():
    secret = 'pa"ss\\word-1234'
    out = main.redact({"a": secret, "b": [f"x {secret} y"]}, {"S": secret})
    assert secret not in json.dumps(out) and out["a"] == "[hidden]"
    assert main.redact({"a": "same"}, {}) == {"a": "same"}


def test_redaction_removes_secrets_from_error_messages(client, fake_http):
    put(client)
    fake_http.reply["response"] = FakeResponse(500, {"e": 1})
    res = run(client, [node("t", "trigger"), node("h", "http", url="https://api.example.com/x?key={{cred.API}}")], [edge("t", "h")])
    assert res["status"] == "failed" and SECRET not in json.dumps(res) and "[hidden]" in res["error"]
    assert fake_http.calls[0]["url"].endswith(SECRET)  # the real request did carry it


# ---------------------------------------------------------------- HTTP authentication
def test_bearer_auth(client, fake_http):
    put(client)
    res = http_flow(client, auth_type="bearer", auth_credential="API")
    assert fake_http.calls[0]["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert SECRET not in json.dumps(res)


def test_basic_auth(client, fake_http):
    put(client, "LOGIN", "user:pa55word")
    http_flow(client, auth_type="basic", auth_credential="LOGIN")
    assert fake_http.calls[0]["headers"]["Authorization"] == "Basic " + base64.b64encode(b"user:pa55word").decode()


def test_api_key_header_auth(client, fake_http):
    put(client)
    http_flow(client, auth_type="header", auth_credential="API", auth_header="X-Api-Key")
    assert fake_http.calls[0]["headers"]["X-Api-Key"] == SECRET


@pytest.mark.parametrize("cfg", [
    {"auth_type": "bearer", "auth_credential": "MISSING"},
    {"auth_type": "bearer", "auth_credential": ""},
    {"auth_type": "weird", "auth_credential": "API"},
    {"auth_type": "header", "auth_credential": "API", "auth_header": "Bad Name"},
    {"auth_type": "header", "auth_credential": "API", "auth_header": "Host"},
])
def test_bad_auth_configuration_fails_without_sending(client, fake_http, cfg):
    put(client)
    assert http_flow(client, **cfg)["status"] == "failed" and fake_http.calls == []


def test_auth_none_adds_nothing(client, fake_http):
    http_flow(client, auth_type="none")
    assert "Authorization" not in fake_http.calls[0]["headers"]


def test_json_body_values_with_quotes_stay_valid(client, fake_http):
    nodes = [node("t", "trigger"), node("i", "input", json=json.dumps({"q": 'say "hi"'})), node("h", "http", url="https://api.example.com/x", method="POST", body='{"text":"{{q}}"}')]
    run(client, nodes, [edge("t", "i"), edge("i", "h")])
    assert fake_http.calls[0]["json"] == {"text": 'say "hi"'}
