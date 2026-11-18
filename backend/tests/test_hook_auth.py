import base64
import json

import pytest

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def secured(client, activate=True, **auth):
    nodes = [node("w", "webhook", sample="{}", **auth), node("o", "output")]
    wid = make_flow(client, nodes, [edge("w", "o")])
    res = client.post(f"/api/workflows/{wid}/active", json={"active": True}) if activate else None
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    return wid, f"/api/hooks/{token}", res


def runs(wid):
    with main.conn() as db: return db.execute("SELECT COUNT(*) c FROM executions WHERE workflow_id=?", (wid,)).fetchone()["c"]


def test_header_token_is_required(client, anon):
    client.put("/api/credentials/HOOKKEY", json={"value": "s3cret-hook-token-value"})
    wid, url, _ = secured(client, auth_type="header", auth_header="X-Api-Key", auth_credential="HOOKKEY")
    assert anon.post(url, json={}).status_code == 401 and runs(wid) == 0
    assert anon.post(url, json={}, headers={"X-Api-Key": "wrong"}).status_code == 401
    assert anon.post(url, json={}, headers={"X-Api-Key": "s3cret-hook-token-value "}).status_code == 401  # exact match only
    assert anon.post(url, json={}, headers={"X-Api-Key": "s3cret-hook-token-value"}).status_code == 200 and runs(wid) == 1


def test_the_default_header_name(client, anon):
    client.put("/api/credentials/HOOKKEY", json={"value": "s3cret-hook-token-value"})
    wid, url, _ = secured(client, auth_type="header", auth_credential="HOOKKEY")
    assert anon.post(url, json={}, headers={"X-Webhook-Token": "s3cret-hook-token-value"}).status_code == 200


def test_basic_authentication(client, anon):
    client.put("/api/credentials/HOOKBASIC", json={"value": "robot:pa55word-long"})
    wid, url, _ = secured(client, auth_type="basic", auth_credential="HOOKBASIC")
    bad = anon.post(url, json={})
    assert bad.status_code == 401 and "Basic" in bad.headers["www-authenticate"]
    enc = lambda s: {"Authorization": "Basic " + base64.b64encode(s.encode()).decode()}
    assert anon.post(url, json={}, headers=enc("robot:wrong")).status_code == 401
    assert anon.post(url, json={}, headers={"Authorization": "Bearer robot:pa55word-long"}).status_code == 401
    assert anon.post(url, json={}, headers={"Authorization": "Basic !!!notbase64"}).status_code == 401
    assert anon.post(url, json={}, headers=enc("robot:pa55word-long")).status_code == 200 and runs(wid) == 1


def test_get_requests_and_forms_are_protected_too(client, anon):
    client.put("/api/credentials/HOOKKEY", json={"value": "s3cret-hook-token-value"})
    fields = json.dumps([{"name": "email", "label": "Email", "type": "email"}])
    wid, url, _ = secured(client, auth_type="header", auth_credential="HOOKKEY", form_fields=fields)
    assert anon.get(url).status_code == 401  # the form is not shown to strangers
    assert anon.get(url, headers={"X-Webhook-Token": "s3cret-hook-token-value"}).status_code == 200


def test_a_deleted_credential_locks_the_webhook_instead_of_opening_it(client, anon):
    client.put("/api/credentials/HOOKKEY", json={"value": "s3cret-hook-token-value"})
    wid, url, _ = secured(client, auth_type="header", auth_credential="HOOKKEY")
    client.delete("/api/credentials/HOOKKEY")
    assert anon.post(url, json={}, headers={"X-Webhook-Token": "s3cret-hook-token-value"}).status_code == 401 and runs(wid) == 0


def test_a_broken_setting_locks_the_webhook(client, anon):
    wid, url, res = secured(client, activate=False, auth_type="bogus", auth_credential="X")
    with main.conn() as db: db.execute("UPDATE workflows SET active=1 WHERE id=?", (wid,))  # forced active, bypassing the check
    assert anon.post(url, json={}).status_code == 401 and runs(wid) == 0


def test_activation_requires_a_credential_and_valid_settings(client):
    for auth in ({"auth_type": "header"}, {"auth_type": "basic", "auth_credential": ""}, {"auth_type": "weird", "auth_credential": "X"}, {"auth_type": "header", "auth_header": "bad header!", "auth_credential": "X"}):
        wid, url, res = secured(client, **auth)
        assert res.status_code == 400, auth


def test_no_authentication_is_the_default_and_manual_runs_ignore_it(client, anon):
    wid, url, res = secured(client)
    assert res.status_code == 200 and anon.post(url, json={}).status_code == 200
    client.put("/api/credentials/HOOKKEY", json={"value": "s3cret-hook-token-value"})
    wid2, url2, _ = secured(client, auth_type="header", auth_credential="HOOKKEY")
    assert client.post(f"/api/workflows/{wid2}/run").json()["status"] == "success"  # the Run button needs no token


def test_the_secret_never_appears_in_results_or_errors(client, anon):
    client.put("/api/credentials/HOOKKEY", json={"value": "s3cret-hook-token-value"})
    wid, url, _ = secured(client, auth_type="header", auth_credential="HOOKKEY")
    res = anon.post(url, json={}, headers={"X-Webhook-Token": "wrong"})
    assert "s3cret-hook-token-value" not in res.text and "s3cret-hook-token-value" not in json.dumps(dict(res.headers))
