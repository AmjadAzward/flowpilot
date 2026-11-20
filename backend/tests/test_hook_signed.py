import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import pytest

from app import main
from conftest import edge, make_flow, node

SECRET = "signing-secret-value-123"


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db: db.execute("DELETE FROM hook_keys")


def runs(wid):
    with main.conn() as db: return db.execute("SELECT COUNT(*) c FROM executions WHERE workflow_id=?", (wid,)).fetchone()["c"]


def hook(client, **cfg):
    client.put("/api/credentials/SIGNKEY", json={"value": SECRET})
    wid = make_flow(client, [node("w", "webhook", sample="{}", **cfg), node("o", "output")], [edge("w", "o")])
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 200
    return wid, f"/api/hooks/{client.get(f'/api/workflows/{wid}').json()['webhook_token']}"


def sign(body: bytes, stamp: str | None = None, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), (stamp.encode() + b"." if stamp else b"") + body, hashlib.sha256).hexdigest()


def test_signed_body_is_required(client, anon):
    wid, url = hook(client, auth_type="hmac", auth_credential="SIGNKEY")
    body = b'{"a": 1}'
    h = {"Content-Type": "application/json"}
    assert anon.post(url, content=body, headers=h).status_code == 401
    assert anon.post(url, content=body, headers={**h, "X-Signature-256": "0" * 64}).status_code == 401
    assert anon.post(url, content=body, headers={**h, "X-Signature-256": sign(body, secret="other")}).status_code == 401
    assert runs(wid) == 0
    assert anon.post(url, content=body, headers={**h, "X-Signature-256": sign(body)}).status_code == 200
    assert anon.post(url, content=body, headers={**h, "X-Signature-256": "sha256=" + sign(body).upper()}).status_code == 200
    assert anon.post(url, content=b'{"a": 2}', headers={**h, "X-Signature-256": sign(body)}).status_code == 401  # signature of another body
    assert runs(wid) == 2


def test_timestamp_blocks_replays_and_is_part_of_the_signature(client, anon):
    wid, url = hook(client, auth_type="hmac", auth_credential="SIGNKEY", auth_header="X-Sig", auth_timestamp_header="X-Ts")
    body = b"{}"
    now = str(int(time.time()))
    old = str(int(time.time()) - 3600)
    assert anon.post(url, content=body, headers={"X-Sig": sign(body, now), "X-Ts": now}).status_code == 200
    assert anon.post(url, content=body, headers={"X-Sig": sign(body, old), "X-Ts": old}).status_code == 401  # valid signature, stale
    assert anon.post(url, content=body, headers={"X-Sig": sign(body, now), "X-Ts": str(int(now) + 1)}).status_code == 401  # timestamp changed
    assert anon.post(url, content=body, headers={"X-Sig": sign(body)}).status_code == 401  # no timestamp
    assert anon.post(url, content=body, headers={"X-Sig": sign(body, "abc"), "X-Ts": "abc"}).status_code == 401
    assert runs(wid) == 1


def test_signed_hooks_never_show_a_form_and_need_a_credential(client, anon):
    wid, url = hook(client, auth_type="hmac", auth_credential="SIGNKEY", form_fields='[{"name":"a"}]')
    assert anon.get(url).status_code == 401
    nodes = [node("w", "webhook", sample="{}", auth_type="hmac"), node("o", "output")]
    w2 = make_flow(client, nodes, [edge("w", "o")])
    assert client.post(f"/api/workflows/{w2}/active", json={"active": True}).status_code == 400


def test_idempotency_key_ignores_repeats(client, anon):
    wid, url = hook(client, idempotency="on")
    assert anon.post(url, json={}, headers={"Idempotency-Key": "k1"}).json().get("status") != "duplicate"
    again = anon.post(url, json={}, headers={"Idempotency-Key": "k1"})
    assert again.status_code == 200 and again.json() == {"status": "duplicate"}
    assert anon.post(url, json={}, headers={"Idempotency-Key": "k2"}).json().get("status") != "duplicate"
    assert anon.post(url, json={}).json().get("status") != "duplicate" and anon.post(url, json={}).json().get("status") != "duplicate"  # no key: always runs
    assert runs(wid) == 4


def test_idempotency_is_off_by_default_and_expires(client, anon):
    wid, url = hook(client)
    anon.post(url, json={}, headers={"Idempotency-Key": "k1"}); anon.post(url, json={}, headers={"Idempotency-Key": "k1"})
    assert runs(wid) == 2
    wid, url = hook(client, idempotency="on")
    anon.post(url, json={}, headers={"Idempotency-Key": "old"})
    with main.conn() as db: db.execute("UPDATE hook_keys SET seen_at='2000-01-01T00:00:00+00:00'")
    assert anon.post(url, json={}, headers={"Idempotency-Key": "old"}).json().get("status") != "duplicate"


# ---- time zones
def test_daily_schedule_in_a_time_zone():
    cfg = main.validate_schedule({"mode": "daily", "time": "09:00", "timezone": "Asia/Tokyo"})
    after = datetime(2030, 1, 1, 0, 30, tzinfo=timezone.utc)  # 09:30 in Tokyo
    assert main.next_run_after(cfg, after) == datetime(2030, 1, 2, 0, 0, tzinfo=timezone.utc)
    assert main.next_run_after(cfg, datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc) - __import__("datetime").timedelta(hours=1)) == datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc)


def test_cron_schedule_follows_daylight_saving():
    cfg = main.validate_schedule({"mode": "cron", "cron": "0 9 * * *", "timezone": "America/New_York"})
    winter = main.next_run_after(cfg, datetime(2030, 1, 10, 0, 0, tzinfo=timezone.utc))
    summer = main.next_run_after(cfg, datetime(2030, 7, 10, 0, 0, tzinfo=timezone.utc))
    assert winter.hour == 14 and summer.hour == 13  # 09:00 in New York is 14:00 / 13:00 UTC


@pytest.mark.parametrize("name", ["Mars/Base", "../etc/passwd", "Europe/Paris; x", "A" * 80, "Europe/Paris/x/y/z"])
def test_bad_time_zones_are_rejected(name):
    with pytest.raises(ValueError): main.validate_schedule({"mode": "daily", "time": "09:00", "timezone": name})


def test_no_time_zone_keeps_the_old_behaviour_and_interval_ignores_it():
    assert "tz" not in main.validate_schedule({"mode": "daily", "time": "09:00"})
    assert main.validate_schedule({"mode": "interval", "minutes": 5, "timezone": "UTC"}) == {"mode": "interval", "minutes": 5}
    after = datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert main.next_run_after(main.validate_schedule({"mode": "daily", "time": "09:00", "timezone": "UTC"}), after) == datetime(2030, 1, 1, 9, 0, tzinfo=timezone.utc)
