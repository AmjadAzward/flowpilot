import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import FakeResponse, edge, make_flow, node

SHEET = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM oauth_pending")
        db.execute("DELETE FROM users WHERE role != 'owner'")


def start(client, **over):
    body = {"name": "GOOGLE", "provider": "google", "client_id": "cid-123", "client_secret": "csecret-456", "scopes": ""}
    body.update(over)
    return client.post("/api/oauth/start", json=body)


def put_connection(name="GOOGLE", access="at-old-token-111", refresh="rt-refresh-222", expires_in=-10, provider="google"):
    main.store_credential(name, json.dumps({"oauth2": 1, "provider": provider, "client_id": "cid-123", "client_secret": "csecret-456",
                                            "token_url": "https://oauth2.googleapis.com/token", "scopes": "x", "access_token": access,
                                            "refresh_token": refresh, "expires_at": time.time() + expires_in}))


def one(client, typ, **data):
    nodes = [node("t", "trigger"), node("i", "input", json='{"name": "Ada"}'), node("x", typ, **data), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "x"), edge("x", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


# ---------------------------------------------------------------- starting a connection
def test_start_builds_a_pkce_authorization_url(client):
    res = start(client)
    assert res.status_code == 200
    url = urlparse(res.json()["auth_url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com" and q["response_type"] == "code" and q["client_id"] == "cid-123"
    assert q["redirect_uri"] == "http://127.0.0.1:5173/api/oauth/callback" and q["access_type"] == "offline"
    assert "spreadsheets" in q["scope"] and q["code_challenge_method"] == "S256"
    assert "csecret-456" not in res.text
    with main.conn() as db:
        row = db.execute("SELECT payload_enc FROM oauth_pending WHERE state=?", (q["state"],)).fetchone()
    assert "csecret-456" not in row["payload_enc"]  # encrypted at rest
    verifier = json.loads(main.fernet().decrypt(row["payload_enc"].encode()))["verifier"]
    assert q["code_challenge"] == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


@pytest.mark.parametrize("over", [{"name": "1bad"}, {"provider": "facebook"}, {"client_id": "has space"}, {"scopes": "bad;scope"},
                                  {"provider": "custom", "auth_url": "http://insecure.example.com/a", "token_url": "https://x.example.com/t"},
                                  {"provider": "custom", "auth_url": "https://a.example.com/a", "token_url": ""}])
def test_start_validation(client, over):
    assert start(client, **over).status_code == 400


def test_custom_provider_and_public_url_override(client, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_PUBLIC_URL", "https://flows.example.com/")
    res = start(client, provider="custom", auth_url="https://id.example.com/authorize?foo=1", token_url="https://id.example.com/token", scopes="read write")
    url = res.json()["auth_url"]
    assert url.startswith("https://id.example.com/authorize?foo=1&response_type=code")
    assert res.json()["redirect_uri"] == "https://flows.example.com/api/oauth/callback"
    assert client.get("/api/oauth/info").json()["redirect_uri"] == "https://flows.example.com/api/oauth/callback"


def test_only_admins_can_start_a_connection(client):
    client.post("/api/users", json={"username": "edith", "password": "long-enough-pw", "role": "editor"})
    e = TestClient(app)
    e.post("/api/auth/login", json={"username": "edith", "password": "long-enough-pw"})
    assert start(e).status_code == 403
    assert e.get("/api/oauth/info").status_code == 200


# ---------------------------------------------------------------- callback
def begin(client):
    url = start(client).json()["auth_url"]
    return parse_qs(urlparse(url).query)["state"][0]


def test_callback_exchanges_the_code_and_stores_an_encrypted_connection(client, anon, fake_http):
    state = begin(client)
    fake_http.reply["response"] = FakeResponse(200, {"access_token": "at-new-token-999", "refresh_token": "rt-new-888", "expires_in": 3600})
    res = anon.get("/api/oauth/callback", params={"code": "the-code", "state": state}, follow_redirects=False)  # no session cookie needed
    assert res.status_code == 303 and res.headers["location"] == "/?oauth=connected"
    call = fake_http.calls[-1]
    assert call["url"] == "https://oauth2.googleapis.com/token" and call["data"]["grant_type"] == "authorization_code"
    assert call["data"]["code"] == "the-code" and call["data"]["client_secret"] == "csecret-456" and call["data"]["code_verifier"]
    listed = next(c for c in client.get("/api/credentials").json() if c["name"] == "GOOGLE")
    assert listed["kind"] == "oauth2" and listed["provider"] == "google"
    assert "at-new-token-999" not in json.dumps(client.get("/api/credentials").json())
    with main.conn() as db:
        enc = db.execute("SELECT value_enc FROM credentials WHERE name='GOOGLE'").fetchone()["value_enc"]
    assert "at-new-token-999" not in enc


def test_state_is_single_use_and_unknown_state_is_rejected(client, anon, fake_http):
    state = begin(client)
    fake_http.reply["response"] = FakeResponse(200, {"access_token": "at-new-token-999", "expires_in": 3600})
    assert "connected" in anon.get("/api/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False).headers["location"]
    again = anon.get("/api/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert "oauth=error" in again.headers["location"]
    assert "oauth=error" in anon.get("/api/oauth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False).headers["location"]
    assert "oauth=error" in anon.get("/api/oauth/callback", follow_redirects=False).headers["location"]


def test_expired_state_is_rejected(client, anon, fake_http):
    state = begin(client)
    with main.conn() as db:
        db.execute("UPDATE oauth_pending SET created_at=?", (time.time() - 2000,))
    assert "oauth=error" in anon.get("/api/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False).headers["location"]


def test_provider_refusal_and_token_failures_do_not_leak_secrets(client, anon, fake_http):
    state = begin(client)
    loc = anon.get("/api/oauth/callback", params={"error": "access_denied", "state": state}, follow_redirects=False).headers["location"]
    assert "oauth=error" in loc and "access_denied" in loc
    state = begin(client)
    fake_http.reply["response"] = FakeResponse(200, {"error": "bad_verification_code", "error_description": "The code csecret-456 is wrong"})
    loc = anon.get("/api/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False).headers["location"]
    assert "oauth=error" in loc and "csecret-456" not in loc
    assert all(c["name"] != "GOOGLE" for c in client.get("/api/credentials").json())


# ---------------------------------------------------------------- tokens
def test_valid_token_is_used_without_refreshing(client, fake_http):
    put_connection(access="at-fresh-token-1", expires_in=3000)
    fake_http.reply["response"] = FakeResponse(200, {"values": [["name"], ["Ada"]]})
    res = one(client, "sheets", credential="GOOGLE", spreadsheet_id=SHEET, range="Sheet1", operation="read")
    assert res["status"] == "success"
    assert len(fake_http.calls) == 1 and fake_http.calls[0]["headers"]["Authorization"] == "Bearer at-fresh-token-1"


def test_expired_token_is_refreshed_saved_and_hidden(client, monkeypatch):
    put_connection(expires_in=-10)
    replies = [{"access_token": "at-refreshed-777", "expires_in": 3600}, {"values": [["name"], ["at-refreshed-777"]]}]
    calls = []

    def fake(method, url, headers, **kw):
        calls.append({"method": method, "url": url, "headers": headers, **kw})
        return replies.pop(0)

    monkeypatch.setattr(main, "do_request", fake)
    monkeypatch.setattr(main, "safe_url", lambda u: u)
    res = one(client, "sheets", credential="GOOGLE", spreadsheet_id=SHEET, range="Sheet1", operation="read")
    assert res["status"] == "success"
    refresh_call, data_call = calls[0], calls[1]
    assert refresh_call["data"]["grant_type"] == "refresh_token" and refresh_call["data"]["refresh_token"] == "rt-refresh-222"
    assert data_call["headers"]["Authorization"] == "Bearer at-refreshed-777"
    assert "at-refreshed-777" not in json.dumps(res)  # echoed back by the sheet, still hidden
    assert json.loads(main.load_secrets()["GOOGLE"])["access_token"] == "at-refreshed-777"


def test_cannot_refresh_without_a_refresh_token(client, fake_http):
    put_connection(refresh="", expires_in=-10)
    res = one(client, "sheets", credential="GOOGLE", spreadsheet_id=SHEET, range="A1", operation="read")
    assert res["status"] == "failed" and "Reconnect" in res["error"]


def test_a_plain_credential_is_not_a_connection(client, fake_http):
    client.put("/api/credentials/PLAIN", json={"value": "just-a-token-123"})
    res = one(client, "sheets", credential="PLAIN", spreadsheet_id=SHEET, range="A1", operation="read")
    assert res["status"] == "failed" and "not an OAuth connection" in res["error"]


def test_connection_cannot_be_inserted_as_text_or_leak_through_data(client, fake_http):
    put_connection(expires_in=3000)
    res = one(client, "template", template="{{cred.GOOGLE}}")
    assert res["status"] == "failed" and "OAuth connection" in res["error"]
    leak = one(client, "input", json='"rt-refresh-222 and csecret-456 and at-old-token-111"')
    assert "rt-refresh-222" not in json.dumps(leak) and "csecret-456" not in json.dumps(leak) and "at-old-token-111" not in json.dumps(leak)


def test_http_node_can_use_an_oauth_connection(client, fake_http):
    put_connection(access="at-fresh-token-1", expires_in=3000)
    one(client, "http", url="https://api.example.com/x", method="GET", auth_type="oauth2", auth_credential="GOOGLE")
    assert fake_http.calls[-1]["headers"]["Authorization"] == "Bearer at-fresh-token-1"


# ---------------------------------------------------------------- Sheets and Gmail
def test_sheets_read_as_objects_or_rows(client, fake_http):
    put_connection(expires_in=3000)
    fake_http.reply["response"] = FakeResponse(200, {"values": [["name", "score"], ["Ada", "91"], ["Bob"]]})
    res = one(client, "sheets", credential="GOOGLE", spreadsheet_id=SHEET, range="Sheet1!A1:B3", operation="read")
    step = next(s for s in res["steps"] if s["node_id"] == "x")
    assert step["output"] == [{"name": "Ada", "score": "91"}, {"name": "Bob", "score": ""}]
    assert "Sheet1%21A1%3AB3" in fake_http.calls[-1]["url"]
    res = one(client, "sheets", credential="GOOGLE", spreadsheet_id=SHEET, range="Sheet1", operation="read", header="false")
    assert next(s for s in res["steps"] if s["node_id"] == "x")["output"] == [["name", "score"], ["Ada", "91"], ["Bob"]]


def test_sheets_append_uses_parameters_safely(client, fake_http):
    put_connection(expires_in=3000)
    fake_http.reply["response"] = FakeResponse(200, {"updates": {"updatedRange": "Sheet1!A5:B5", "updatedRows": 1}})
    res = one(client, "sheets", credential="GOOGLE", spreadsheet_id=SHEET, range="Sheet1", operation="append", values='["{{name}}", "x\\"y"]')
    step = next(s for s in res["steps"] if s["node_id"] == "x")
    assert step["output"] == {"updated_range": "Sheet1!A5:B5", "updated_rows": 1}
    call = fake_http.calls[-1]
    assert call["method"] == "POST" and ":append" in call["url"] and call["json"] == {"values": [["Ada", 'x"y']]}


@pytest.mark.parametrize("data", [{"spreadsheet_id": "short"}, {"spreadsheet_id": SHEET + "/../x"}, {"range": "A1?x=1"}, {"operation": "delete"}, {"operation": "append", "values": ""},
                                  {"operation": "append", "values": "{bad"}, {"operation": "append", "values": "[]"}])
def test_sheets_validation(client, fake_http, data):
    put_connection(expires_in=3000)
    base = {"credential": "GOOGLE", "spreadsheet_id": SHEET, "range": "Sheet1", "operation": "read"}
    base.update(data)
    assert one(client, "sheets", **base)["status"] == "failed"


def test_gmail_sends_a_message(client, fake_http):
    put_connection(expires_in=3000)
    res = one(client, "gmail", credential="GOOGLE", to="a@example.com, b@example.com", subject="Hi {{name}}", body="Hello {{name}}")
    assert res["status"] == "success"
    call = fake_http.calls[-1]
    assert call["url"].endswith("/users/me/messages/send")
    raw = base64.urlsafe_b64decode(call["json"]["raw"]).decode()
    assert "Subject: Hi Ada" in raw and "To: a@example.com, b@example.com" in raw and "Hello Ada" in raw


@pytest.mark.parametrize("data", [{"to": ""}, {"to": "not-an-email"}, {"to": "a@example.com", "subject": "x\r\nBcc: evil@example.com"}])
def test_gmail_validation_blocks_header_injection(client, fake_http, data):
    put_connection(expires_in=3000)
    base = {"credential": "GOOGLE", "to": "a@example.com", "subject": "s", "body": "b"}
    base.update(data)
    assert one(client, "gmail", **base)["status"] == "failed"


def test_new_types_are_accepted(client):
    for t in ("sheets", "gmail"):
        make_flow(client, [node("t", "trigger"), node("x", t)], [edge("t", "x")])
