import base64
import json
import time
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from app import main
from app.oidc import verify_id_token

ISSUER = "https://login.example.com"
CLIENT = "flowpilot-client"
SECRET = "super-secret-client-value"


def b64(data: bytes) -> str: return base64.urlsafe_b64encode(data).decode().rstrip("=")


class Idp:
    """A stand-in identity provider: a real RSA key signs real RS256 tokens, and the discovery, keys and token endpoints answer from memory."""

    def __init__(self, bits=2048, kid="key-1"):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
        self.kid = kid
        self.requests = []
        self.next_claims = {}
        self.token_error = None

    def jwk(self, kid=None):
        n = self.key.public_key().public_numbers()
        return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid or self.kid, "n": b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")), "e": b64(n.e.to_bytes(3, "big"))}

    def token(self, claims, alg="RS256", kid=None, key=None, tamper=False):
        header = {"alg": alg, "typ": "JWT", "kid": kid or self.kid}
        signing = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}"
        sig = (key or self.key).sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
        if tamper: signing = f"{b64(json.dumps(header).encode())}.{b64(json.dumps({**claims, 'email': 'admin@evil.com'}).encode())}"
        return f"{signing}.{b64(sig)}"

    def claims(self, nonce, **over):
        now = int(time.time())
        return {"iss": ISSUER, "aud": CLIENT, "sub": "user-1", "email": "ada@company.com", "email_verified": True, "iat": now, "exp": now + 600, "nonce": nonce, **over}

    def fake(self, method, url, headers, **kw):
        self.requests.append((method, url, kw))
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize", "token_endpoint": ISSUER + "/token", "jwks_uri": ISSUER + "/keys"}
        if url == ISSUER + "/keys": return {"keys": [self.jwk()]}
        if url == ISSUER + "/token":
            if self.token_error: raise ValueError(self.token_error)
            return {"id_token": self.id_token, "access_token": "unused"}
        raise AssertionError("unexpected request to " + url)


@pytest.fixture()
def idp(monkeypatch):
    p = Idp()
    monkeypatch.setattr(main, "do_request", p.fake)
    monkeypatch.setattr(main, "safe_url", lambda u: u)
    main._sso_cache.clear()
    yield p
    main._sso_cache.clear()


@pytest.fixture(autouse=True)
def _clean(client):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM settings WHERE key LIKE 'sso_%'")
        db.execute("DELETE FROM credentials WHERE name='SSO_CLIENT_SECRET'")
        db.execute("DELETE FROM oauth_pending")
        db.execute("DELETE FROM users WHERE auth_source='sso' OR username='local@company.com'")
    main._sso_cache.clear()


def configure(client, **over):
    body = {"enabled": True, "issuer": ISSUER, "client_id": CLIENT, "client_secret": SECRET, "allowed_domains": "company.com", "default_role": "viewer", **over}
    res = client.put("/api/sso/config", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def begin(browser):
    """Start a sign-in and return (state, nonce) as the provider would receive them."""
    res = browser.get("/api/auth/sso/start", follow_redirects=False)
    assert res.status_code == 303, res.text
    q = parse_qs(urlparse(res.headers["location"]).query)
    return res, q["state"][0], q["nonce"][0], q


def finish(browser, idp, state, nonce, claims=None, **kw):
    idp.id_token = idp.token({**idp.claims(nonce), **(claims or {})}, **kw)
    return browser.get("/api/auth/sso/callback", params={"code": "auth-code-1", "state": state}, follow_redirects=False)


# ---------------------------------------------------------------- the happy path
def test_full_sign_in_creates_an_account_and_a_session(client, idp):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    assert res.headers["location"].startswith(ISSUER + "/authorize?")
    assert q["client_id"] == [CLIENT] and q["response_type"] == ["code"] and q["scope"] == ["openid email profile"] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["http://127.0.0.1:5173/api/auth/sso/callback"]
    done = finish(browser, idp, state, nonce)
    assert done.status_code == 303 and done.headers["location"] == "/"
    assert "fp_session" in done.headers["set-cookie"] and "httponly" in done.headers["set-cookie"].lower()
    me = browser.get("/api/auth/status").json()
    assert me["authenticated"] and me["username"] == "ada@company.com" and me["role"] == "viewer"
    token_call = next(r for r in idp.requests if r[1].endswith("/token"))[2]["data"]
    assert token_call["client_secret"] == SECRET and token_call["grant_type"] == "authorization_code" and token_call["code"] == "auth-code-1" and len(token_call["code_verifier"]) >= 43


def test_the_pkce_challenge_matches_the_verifier_sent_later(client, idp):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    finish(browser, idp, state, nonce)
    verifier = next(r for r in idp.requests if r[1].endswith("/token"))[2]["data"]["code_verifier"]
    import hashlib
    assert q["code_challenge"] == [b64(hashlib.sha256(verifier.encode()).digest())]


def test_a_second_sign_in_reuses_the_account_and_the_default_role_applies_once(client, idp):
    configure(client, default_role="editor")
    for _ in range(2):
        browser = TestClient(main.app)
        res, state, nonce, q = begin(browser)
        assert finish(browser, idp, state, nonce).headers["location"] == "/"
    with main.conn() as db: assert db.execute("SELECT COUNT(*) c, MIN(role) r FROM users WHERE username='ada@company.com'").fetchone()["c"] == 1
    uid = next(u["id"] for u in client.get("/api/users").json() if u["username"] == "ada@company.com")
    client.put(f"/api/users/{uid}", json={"role": "admin"})
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    finish(browser, idp, state, nonce)
    assert browser.get("/api/auth/status").json()["role"] == "admin"  # a promotion is not undone by signing in again


def test_the_sso_account_is_listed_and_has_no_password(client, idp):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    finish(browser, idp, state, nonce)
    row = next(u for u in client.get("/api/users").json() if u["username"] == "ada@company.com")
    assert row["auth_source"] == "sso" and "password" not in json.dumps(row)
    assert TestClient(main.app).post("/api/auth/login", json={"username": "ada@company.com", "password": "sso$no-password"}).status_code == 401
    assert TestClient(main.app).post("/api/auth/login", json={"username": "ada@company.com", "password": ""}).status_code in (401, 422)
    assert client.put(f"/api/users/{row['id']}", json={"password": "a-brand-new-password"}).status_code == 400
    assert browser.post("/api/auth/password", json={"current": "x", "new": "a-brand-new-password"}).status_code == 400


def test_status_tells_the_login_page_when_sso_is_ready(client, idp):
    anon = TestClient(main.app)
    assert anon.get("/api/auth/status").json()["sso"]["enabled"] is False
    configure(client)
    assert anon.get("/api/auth/status").json()["sso"]["enabled"] is True
    configure(client, enabled=False)
    assert anon.get("/api/auth/status").json()["sso"]["enabled"] is False
    assert anon.get("/api/auth/sso/start", follow_redirects=False).status_code == 404


def test_sign_ins_are_recorded_in_the_activity_log(client, idp):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    finish(browser, idp, state, nonce)
    assert any(r["method"] == "SSO" and r["username"] == "ada@company.com" for r in client.get("/api/audit").json())


# ---------------------------------------------------------------- things that must be refused
def refused(res, text=None):
    assert res.status_code == 303 and res.headers["location"].startswith("/?sso=error"), res.headers.get("location")
    assert "set-cookie" not in res.headers
    if text: assert text.lower() in unquote(res.headers["location"]).lower(), res.headers["location"]


def test_a_tampered_token_is_refused(client, idp):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    refused(finish(browser, idp, state, nonce, tamper=True), "signature")


def test_a_token_signed_by_someone_else_is_refused(client, idp):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    refused(finish(browser, idp, state, nonce, key=other), "signature")


def test_wrong_claims_are_refused(client, idp):
    configure(client)
    cases = [({"iss": "https://evil.example.com"}, "issuer"), ({"aud": "someone-else"}, "not issued for this application"), ({"exp": int(time.time()) - 3600}, "expired"),
             ({"nonce": "another"}, "sign-in attempt"), ({"email_verified": False}, "confirm your email"), ({"email": ""}, "confirm your email"), ({"email": "ada@other.org"}, "domain"),
             ({"sub": ""}, "subject"), ({"aud": [CLIENT, "x"]}, "several audiences")]
    for claims, text in cases:
        browser = TestClient(main.app)
        res, state, nonce, q = begin(browser)
        refused(finish(browser, idp, state, nonce, claims=claims), text)
    with main.conn() as db: assert db.execute("SELECT COUNT(*) c FROM users WHERE auth_source='sso'").fetchone()["c"] == 0


def test_only_rs256_is_accepted_so_none_and_hmac_tricks_fail(client, idp):
    configure(client)
    for alg in ("none", "HS256", "RS512"):
        browser = TestClient(main.app)
        res, state, nonce, q = begin(browser)
        header = b64(json.dumps({"alg": alg, "kid": idp.kid}).encode())
        body = b64(json.dumps(idp.claims(nonce)).encode())
        idp.id_token = f"{header}.{body}." + ("" if alg == "none" else b64(b"forged"))
        refused(browser.get("/api/auth/sso/callback", params={"code": "c", "state": state}, follow_redirects=False), "algorithm")


def test_a_replayed_or_invented_state_is_refused(client, idp):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    assert finish(browser, idp, state, nonce).headers["location"] == "/"
    refused(finish(TestClient(main.app), idp, state, nonce), "expired or was not started")  # single use
    refused(TestClient(main.app).get("/api/auth/sso/callback", params={"code": "x", "state": "invented"}, follow_redirects=False))
    refused(TestClient(main.app).get("/api/auth/sso/callback", follow_redirects=False))


def test_a_state_older_than_ten_minutes_is_refused(client, idp):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    with main.conn() as db: db.execute("UPDATE oauth_pending SET created_at=?", (time.time() - 3600,))
    refused(finish(browser, idp, state, nonce), "expired")


def test_an_oauth_connection_state_cannot_be_used_for_sso(client, idp):
    configure(client)
    r = client.post("/api/oauth/start", json={"name": "GOOGLE", "provider": "google", "client_id": "cid", "client_secret": "csecret", "scopes": ""})
    state = parse_qs(urlparse(r.json()["auth_url"]).query)["state"][0]
    refused(TestClient(main.app).get("/api/auth/sso/callback", params={"code": "x", "state": state}, follow_redirects=False))


def test_the_provider_refusing_or_failing_is_a_clean_error(client, idp):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    refused(browser.get("/api/auth/sso/callback", params={"error": "access_denied", "state": state}, follow_redirects=False), "access_denied")
    res, state, nonce, q = begin(browser)
    idp.token_error = "invalid_grant for code " + "auth-code-1" + " secret " + SECRET
    res2 = browser.get("/api/auth/sso/callback", params={"code": "auth-code-1", "state": state}, follow_redirects=False)
    refused(res2, "invalid_grant")
    assert SECRET not in res2.headers["location"] and "auth-code-1" not in res2.headers["location"]


def test_a_local_account_is_never_taken_over(client, idp):
    client.post("/api/users", json={"username": "local@company.com", "password": "long-enough-pw", "role": "admin"})
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    refused(finish(browser, idp, state, nonce, claims={"email": "local@company.com"}), "local account")
    assert browser.get("/api/auth/status").json()["authenticated"] is False


def test_addresses_too_long_for_a_username_are_refused(client, idp):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    refused(finish(browser, idp, state, nonce, claims={"email": "a" * 40 + "@company.com"}), "40")


def test_the_user_limit_still_applies(client, idp, monkeypatch):
    configure(client)
    monkeypatch.setattr(main, "MAX_USERS", 1)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    refused(finish(browser, idp, state, nonce), "Maximum")


def test_rotated_keys_are_picked_up_once(client, idp, monkeypatch):
    configure(client)
    browser = TestClient(main.app)
    res, state, nonce, q = begin(browser)
    stale = idp.jwk(kid="old-key")
    main._sso_cache["jwks:" + ISSUER + "/keys"] = (time.time(), [stale])  # the cache holds an out-of-date key list
    assert finish(browser, idp, state, nonce).headers["location"] == "/"


# ---------------------------------------------------------------- configuration
def test_only_the_owner_can_configure_and_the_secret_is_never_returned(client, idp):
    cfg = configure(client)
    assert cfg["ready"] is True and cfg["has_secret"] is True and SECRET not in json.dumps(cfg) and cfg["redirect_uri"].endswith("/api/auth/sso/callback")
    assert SECRET not in json.dumps(client.get("/api/sso/config").json()) and SECRET not in json.dumps(client.get("/api/credentials").json())
    with main.conn() as db: assert SECRET not in db.execute("SELECT value_enc FROM credentials WHERE name='SSO_CLIENT_SECRET'").fetchone()["value_enc"]
    for role in ("viewer", "editor", "admin"):
        client.post("/api/users", json={"username": f"sso-{role}", "password": "long-enough-pw", "role": role})
        c = TestClient(main.app)
        c.post("/api/auth/login", json={"username": f"sso-{role}", "password": "long-enough-pw"})
        assert c.get("/api/sso/config").status_code == 403 and c.put("/api/sso/config", json={}).status_code == 403 and c.post("/api/sso/test").status_code == 403
    for u in client.get("/api/users").json():
        if u["username"].startswith("sso-"): client.delete(f"/api/users/{u['id']}")


def test_validation(client, idp):
    for bad in ({"issuer": "ftp://x.com"}, {"issuer": "http://login.example.com"}, {"issuer": "https://a b.com"}, {"allowed_domains": "not a domain"}, {"allowed_domains": "company"},
                {"default_role": "owner"}, {"client_id": "has space"}, {"client_secret": "has space"}):
        assert client.put("/api/sso/config", json={"enabled": True, "issuer": ISSUER, "client_id": CLIENT, "client_secret": SECRET, "allowed_domains": "company.com", **bad}).status_code == 400, bad
    # turning it on needs everything
    assert client.put("/api/sso/config", json={"enabled": True, "issuer": ISSUER, "client_id": CLIENT, "allowed_domains": "company.com"}).status_code == 400
    assert client.get("/api/sso/config").json()["enabled"] is False


def test_the_secret_can_be_left_blank_to_keep_it(client, idp):
    configure(client)
    again = client.put("/api/sso/config", json={"enabled": True, "issuer": ISSUER, "client_id": CLIENT, "allowed_domains": "company.com, @Other.org", "default_role": "viewer"}).json()
    assert again["ready"] is True and again["allowed_domains"] == "company.com,other.org"


def test_the_connection_test_reports_what_it_found(client, idp):
    configure(client)
    res = client.post("/api/sso/test")
    assert res.status_code == 200 and res.json()["ok"] and res.json()["keys"] == 1 and res.json()["token_endpoint"] == ISSUER + "/token"


def test_the_connection_test_reports_a_mismatched_issuer(client, idp, monkeypatch):
    configure(client)
    monkeypatch.setattr(main, "do_request", lambda *a, **k: {"issuer": "https://other.example.com", "authorization_endpoint": "https://x/a", "token_endpoint": "https://x/t", "jwks_uri": "https://x/k"})
    main._sso_cache.clear()
    res = client.post("/api/sso/test")
    assert res.status_code == 400 and "issuer" in res.json()["detail"]


def test_private_provider_addresses_are_blocked_unless_allowed(client, monkeypatch):
    monkeypatch.undo()
    assert client.put("/api/sso/config", json={"enabled": False, "issuer": "http://127.0.0.1:8080/realms/x"}).status_code == 400


# ---------------------------------------------------------------- the verifier on its own
def test_verifier_accepts_a_good_token_and_checks_each_claim(idp):
    keys = [idp.jwk()]
    good = idp.token(idp.claims("n1"))
    assert verify_id_token(good, keys, issuer=ISSUER, client_id=CLIENT, nonce="n1")["email"] == "ada@company.com"
    with pytest.raises(ValueError, match="not a signed token"): verify_id_token("abc", keys, issuer=ISSUER, client_id=CLIENT, nonce="n1")
    with pytest.raises(ValueError, match="missing or too large"): verify_id_token("a." * 9000, keys, issuer=ISSUER, client_id=CLIENT, nonce="n1")
    with pytest.raises(ValueError, match="signing key not found"): verify_id_token(good, [], issuer=ISSUER, client_id=CLIENT, nonce="n1")
    with pytest.raises(ValueError, match="signing key not found"): verify_id_token(good, [idp.jwk(kid="other")], issuer=ISSUER, client_id=CLIENT, nonce="n1")
    with pytest.raises(ValueError, match="not valid yet"): verify_id_token(idp.token(idp.claims("n1", iat=int(time.time()) + 9999)), keys, issuer=ISSUER, client_id=CLIENT, nonce="n1")
    with pytest.raises(ValueError, match="nonce|sign-in attempt"): verify_id_token(good, keys, issuer=ISSUER, client_id=CLIENT, nonce="")
    assert verify_id_token(idp.token(idp.claims("n1", aud=[CLIENT, "x"], azp=CLIENT)), keys, issuer=ISSUER, client_id=CLIENT, nonce="n1")["sub"] == "user-1"
    assert verify_id_token(idp.token(idp.claims("n1", exp=int(time.time()) - 30)), keys, issuer=ISSUER, client_id=CLIENT, nonce="n1")  # within the clock-skew allowance


def test_verifier_rejects_weak_and_malformed_keys(idp):
    weak = Idp(bits=1024)
    with pytest.raises(ValueError, match="too weak"): verify_id_token(weak.token(weak.claims("n")), [weak.jwk()], issuer=ISSUER, client_id=CLIENT, nonce="n")
    with pytest.raises(ValueError, match="malformed"): verify_id_token(idp.token(idp.claims("n")), [{"kty": "RSA", "kid": idp.kid}], issuer=ISSUER, client_id=CLIENT, nonce="n")
    not_json = f"{b64(b'not json')}.{b64(b'{}')}.{b64(b'x')}"
    with pytest.raises(ValueError, match="header is not valid JSON"): verify_id_token(not_json, [idp.jwk()], issuer=ISSUER, client_id=CLIENT, nonce="n")
