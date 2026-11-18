import pytest

from app import main
from conftest import OWNER


@pytest.fixture(autouse=True)
def _clean_limits(session_cookie):
    main._failed_logins.clear()
    yield
    main._failed_logins.clear()


def test_status_is_public_and_reports_state(anon, session_cookie):
    body = anon.get("/api/auth/status").json()
    assert body == {"setup_required": False, "authenticated": False, "username": None, "role": None}


def test_status_when_signed_in(client):
    body = client.get("/api/auth/status").json()
    assert body["authenticated"] is True and body["username"] == "owner"


def test_health_stays_public(anon):
    assert anon.get("/api/health").status_code == 200


@pytest.mark.parametrize("method,path", [
    ("get", "/api/workflows"),
    ("get", "/api/workflows/demo-workflow"),
    ("post", "/api/workflows/demo-workflow/run"),
    ("post", "/api/workflows/demo-workflow/duplicate"),
    ("delete", "/api/workflows/demo-workflow"),
    ("get", "/api/executions"),
    ("delete", "/api/executions"),
])
def test_protected_endpoints_require_login(anon, method, path):
    res = getattr(anon, method)(path)
    assert res.status_code == 401
    assert res.json()["detail"] == "Not signed in"


def test_forged_cookie_rejected(anon):
    anon.cookies.set("fp_session", "not-a-real-token")
    assert anon.get("/api/workflows").status_code == 401


def test_setup_closed_once_account_exists(anon):
    res = anon.post("/api/auth/setup", json={"username": "intruder", "password": "another-password"})
    assert res.status_code == 409


def test_login_success_sets_httponly_strict_cookie(anon):
    res = anon.post("/api/auth/login", json=OWNER)
    assert res.status_code == 200 and res.json()["username"] == "owner"
    header = res.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=strict" in header
    assert anon.get("/api/workflows").status_code == 200


def test_login_username_is_case_insensitive(anon):
    assert anon.post("/api/auth/login", json={"username": "  OWNER ", "password": OWNER["password"]}).status_code == 200


@pytest.mark.parametrize("creds", [
    {"username": "owner", "password": "wrong-password"},
    {"username": "nobody", "password": "whatever-password"},
])
def test_login_rejects_bad_credentials_with_same_message(anon, creds):
    res = anon.post("/api/auth/login", json=creds)
    assert res.status_code == 401
    assert res.json()["detail"] == "Incorrect username or password"
    assert "fp_session" not in res.cookies


def test_login_locks_out_after_repeated_failures(anon):
    for _ in range(main.MAX_FAILED_LOGINS):
        assert anon.post("/api/auth/login", json={"username": "owner", "password": "nope-nope"}).status_code == 401
    assert anon.post("/api/auth/login", json={"username": "owner", "password": "nope-nope"}).status_code == 429
    # even the right password is refused while locked
    assert anon.post("/api/auth/login", json=OWNER).status_code == 429


def test_successful_login_resets_failure_counter(anon):
    for _ in range(main.MAX_FAILED_LOGINS - 1):
        anon.post("/api/auth/login", json={"username": "owner", "password": "nope-nope"})
    assert anon.post("/api/auth/login", json=OWNER).status_code == 200
    assert anon.post("/api/auth/login", json={"username": "owner", "password": "nope-nope"}).status_code == 401


def test_logout_invalidates_session(anon):
    anon.post("/api/auth/login", json=OWNER)
    token = anon.cookies.get("fp_session")
    assert anon.post("/api/auth/logout").status_code == 204
    anon.cookies.set("fp_session", token)  # replay the old token
    assert anon.get("/api/workflows").status_code == 401


def test_expired_session_rejected(anon):
    anon.post("/api/auth/login", json=OWNER)
    mine = main.token_hash(anon.cookies.get("fp_session"))
    with main.conn() as db:  # expire only this test's own session, not the shared one
        db.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00' WHERE token_hash=?", (mine,))
    assert anon.get("/api/workflows").status_code == 401


def test_passwords_are_hashed_and_salted(client):
    with main.conn() as db:
        stored = db.execute("SELECT password_hash FROM users").fetchone()["password_hash"]
    assert OWNER["password"] not in stored and stored.startswith("pbkdf2_sha256$")
    assert main.verify_password(OWNER["password"], stored)
    assert not main.verify_password("wrong", stored)
    assert main.hash_password("x") != main.hash_password("x")  # random salt


def test_verify_password_handles_garbage():
    assert main.verify_password("x", "garbage") is False


def test_session_tokens_are_not_stored_in_plaintext(anon):
    anon.post("/api/auth/login", json=OWNER)
    token = anon.cookies.get("fp_session")
    with main.conn() as db:
        hashes = [r["token_hash"] for r in db.execute("SELECT token_hash FROM sessions")]
    assert token not in hashes and main.token_hash(token) in hashes
