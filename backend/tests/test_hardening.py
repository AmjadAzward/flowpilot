import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient

from app import main
from app.main import app
from conftest import OWNER, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


# ---------------------------------------------------------------- headers, sizes, cookies
def test_security_headers_are_on_every_response(client, anon):
    for res in (client.get("/api/workflows"), anon.get("/api/workflows"), anon.get("/api/health")):
        assert res.headers["x-content-type-options"] == "nosniff" and res.headers["referrer-policy"] == "no-referrer" and res.headers["x-frame-options"] == "DENY"
    assert client.get("/api/workflows").headers["cache-control"] == "no-store"


def test_webhook_responses_are_not_marked_no_store(client, anon):
    wid = make_flow(client, [node("w", "webhook"), node("o", "output")], [edge("w", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    res = anon.post(f"/api/hooks/{token}", json={})
    assert "no-store" not in res.headers.get("cache-control", "") and res.headers["x-content-type-options"] == "nosniff"


def test_oversized_requests_are_refused_before_processing(client):
    big = b"x" * (6 * 1024 * 1024)
    assert client.post("/api/workflows", content=big, headers={"content-type": "application/json"}).status_code == 413
    assert client.put("/api/credentials/BIGONE", content=big, headers={"content-type": "application/json"}).status_code == 413
    assert client.post("/api/auth/login", content=big, headers={"content-type": "application/json"}).status_code == 413
    ok = client.post("/api/workflows", json={"name": "fits", "description": "", "nodes": [node("t", "trigger")], "edges": []})
    assert ok.status_code == 201


def test_backup_restore_accepts_larger_bodies_than_normal_requests(client):
    body = b'{"format": "flowpilot-backup", "workflows": [], "pad": "' + b"x" * (6 * 1024 * 1024) + b'"}'
    assert client.post("/api/backup/restore", content=body, headers={"content-type": "application/json"}).status_code != 413


def test_hooks_keep_their_own_smaller_limit(client, anon):
    wid = make_flow(client, [node("w", "webhook"), node("o", "output")], [edge("w", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    assert anon.post(f"/api/hooks/{token}", content=b"x" * (300 * 1024)).status_code == 413


def test_session_cookie_is_secure_only_behind_https():
    plain = TestClient(app).post("/api/auth/login", json=OWNER)
    assert "secure" not in plain.headers["set-cookie"].lower() and "httponly" in plain.headers["set-cookie"].lower() and "samesite=strict" in plain.headers["set-cookie"].lower()
    proxied = TestClient(app).post("/api/auth/login", json=OWNER, headers={"x-forwarded-proto": "https"})
    assert "secure" in proxied.headers["set-cookie"].lower()
    direct = TestClient(app, base_url="https://testserver").post("/api/auth/login", json=OWNER)
    assert "secure" in direct.headers["set-cookie"].lower()


# ---------------------------------------------------------------- health
def test_health_reports_database_scheduler_and_queue(anon, monkeypatch):
    body = anon.get("/api/health").json()
    assert body["status"] == "ok" and body["queue"] is False and body["scheduler"] == "off" and body["version"]
    monkeypatch.setenv("FLOWPILOT_QUEUE", "1")
    assert anon.get("/api/health").json()["queue"] is True


def test_health_degrades_when_the_database_fails(anon, monkeypatch):
    def broken(): raise RuntimeError("disk gone")
    monkeypatch.setattr(main, "conn", broken)
    assert anon.get("/api/health").json()["status"] == "degraded"


# ---------------------------------------------------------------- key rotation
def key_lines():
    return main.key_path().read_bytes().split()


def cipher(name):
    with main.conn() as db:
        return db.execute("SELECT value_enc FROM credentials WHERE name=?", (name,)).fetchone()["value_enc"]


def test_rotation_reencrypts_with_a_new_key_and_keeps_the_old_as_fallback(client):
    client.put("/api/credentials/ROT_ONE", json={"value": "first-secret-value"})
    client.put("/api/credentials/ROT_TWO", json={"value": "second-secret-value"})
    before_keys, before_cipher = key_lines(), cipher("ROT_ONE")
    assert main.rotate_encryption_key() >= 2
    after = key_lines()
    assert len(after) == len(before_keys) + 1 and after[1:] == before_keys and after[0] != before_keys[0]
    assert cipher("ROT_ONE") != before_cipher
    assert main.load_secrets()["ROT_ONE"] == "first-secret-value" and main.load_secrets()["ROT_TWO"] == "second-secret-value"
    Fernet(after[0]).decrypt(cipher("ROT_ONE").encode())  # now encrypted with the new primary key
    with pytest.raises(InvalidToken): Fernet(before_keys[0]).decrypt(cipher("ROT_ONE").encode())


def test_dropping_old_keys_leaves_one_key_and_everything_still_works(client):
    client.put("/api/credentials/ROT_DROP", json={"value": "drop-secret-value"})
    old = key_lines()[0]
    main.rotate_encryption_key(drop_old=True)
    assert len(key_lines()) == 1 and key_lines()[0] != old
    assert main.load_secrets()["ROT_DROP"] == "drop-secret-value"
    with pytest.raises(InvalidToken): Fernet(old).decrypt(cipher("ROT_DROP").encode())  # a stale key copy is now useless
    wid = make_flow(client, [node("t", "trigger"), node("m", "template", template="{{cred.ROT_DROP}}!"), node("o", "output")], [edge("t", "m"), edge("m", "o")])
    run = client.post(f"/api/workflows/{wid}/run").json()
    assert run["status"] == "success" and "drop-secret-value" not in str(run)


def test_rotation_with_nothing_saved_and_repeated_rotation(client):
    with main.conn() as db: db.execute("DELETE FROM credentials")
    assert main.rotate_encryption_key() == 0 and main.rotate_encryption_key(drop_old=True) == 0
    client.put("/api/credentials/AGAIN", json={"value": "again-secret-value"})
    for _ in range(3): main.rotate_encryption_key()
    assert main.load_secrets()["AGAIN"] == "again-secret-value"
    main.rotate_encryption_key(drop_old=True)
    assert len(key_lines()) == 1 and main.load_secrets()["AGAIN"] == "again-secret-value"


def test_a_credential_that_cannot_be_decrypted_aborts_rotation_without_changes(client):
    client.put("/api/credentials/GOOD_ONE", json={"value": "good-secret-value"})
    with main.conn() as db:
        db.execute("INSERT INTO credentials (name,value_enc,created_at,updated_at) VALUES ('BAD_ONE', ?, 'x', 'x')", (Fernet(Fernet.generate_key()).encrypt(b"foreign").decode(),))
    before = cipher("GOOD_ONE")
    with pytest.raises(InvalidToken): main.rotate_encryption_key()
    assert cipher("GOOD_ONE") == before and main.load_secrets()["GOOD_ONE"] == "good-secret-value"
    with main.conn() as db: db.execute("DELETE FROM credentials WHERE name='BAD_ONE'")


def test_a_legacy_single_line_key_file_still_works():
    assert len(key_lines()) >= 1
    token = main.fernet().encrypt(b"x")
    assert Fernet(key_lines()[0]).decrypt(token) == b"x"


# ---------------------------------------------------------------- inbox polling off the scheduler thread
def test_inbox_checks_run_in_the_background_and_never_overlap(client, monkeypatch):
    monkeypatch.setattr(main, "INBOX_ASYNC", True)
    started, release, calls = threading.Event(), threading.Event(), []

    def slow_poll(doc, node_):
        calls.append(doc["id"])
        started.set()
        release.wait(5)

    monkeypatch.setattr(main, "poll_inbox", slow_poll)
    client.put("/api/credentials/MAIL", json={"value": "imaps://u:p@imap.example.com"})
    wid = make_flow(client, [node("e", "email_in", credential="MAIL", minutes="5"), node("o", "output")], [edge("e", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    due = lambda: main.conn().execute("UPDATE workflows SET next_run_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), wid)).connection.commit()
    due()
    t0 = time.time()
    assert main.scheduler_tick() == 1
    assert time.time() - t0 < 1.0  # the tick did not wait for the slow inbox
    assert started.wait(3)
    due()
    main.scheduler_tick()
    assert calls == [wid]  # a second check was not started while the first is still running
    release.set()
    for _ in range(50):
        if wid not in main._inbox_busy: break
        time.sleep(0.05)
    assert wid not in main._inbox_busy
    due()
    main.scheduler_tick()
    for _ in range(50):
        if len(calls) == 2: break
        time.sleep(0.05)
    assert len(calls) == 2
