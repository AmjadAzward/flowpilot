import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def hook(client, **cfg):
    wid = make_flow(client, [node("w", "webhook", sample="{}", **cfg), node("o", "output")], [edge("w", "o")])
    res = client.post(f"/api/workflows/{wid}/active", json={"active": True})
    return wid, f"/api/hooks/{client.get(f'/api/workflows/{wid}').json()['webhook_token']}", res


def from_ip(ip):
    return TestClient(main.app, client=(ip, 50000))


def runs(wid):
    with main.conn() as db: return db.execute("SELECT COUNT(*) c FROM executions WHERE workflow_id=?", (wid,)).fetchone()["c"]


def test_only_listed_addresses_may_call(client):
    wid, url, _ = hook(client, allowed_ips="203.0.113.7, 10.0.0.0/8 2001:db8::/32")
    assert from_ip("198.51.100.1").post(url, json={}).status_code == 403 and runs(wid) == 0
    for ok in ("203.0.113.7", "10.9.8.7", "2001:db8::5"):
        assert from_ip(ok).post(url, json={}).status_code == 200, ok
    assert from_ip("203.0.113.8").post(url, json={}).status_code == 403
    assert runs(wid) == 3


def test_no_list_means_anyone_and_ipv4_in_ipv6_is_matched(client):
    wid, url, _ = hook(client)
    assert from_ip("198.51.100.1").post(url, json={}).status_code == 200
    wid2, url2, _ = hook(client, allowed_ips="192.0.2.0/24")
    assert from_ip("::ffff:192.0.2.5").post(url2, json={}).status_code == 200
    assert from_ip("2001:db8::1").post(url2, json={}).status_code == 403  # a v6 caller never matches a v4 rule


@pytest.mark.parametrize("rules", ["not-an-ip", "1.2.3.4/99", "1.2.3.4; 5.6.7.8", ",".join(f"10.0.0.{i}" for i in range(51))])
def test_a_bad_list_cannot_be_activated(client, rules):
    _, _, res = hook(client, allowed_ips=rules)
    assert res.status_code == 400


def test_a_list_that_became_broken_refuses_everyone(client):
    wid, url, _ = hook(client, allowed_ips="10.0.0.0/8")
    with main.conn() as db:
        row = db.execute("SELECT definition FROM workflows WHERE id=?", (wid,)).fetchone()
        import json
        doc = json.loads(row["definition"])
        for n in doc["nodes"]:
            if n["type"] == "webhook": n["data"]["allowed_ips"] = "garbage"
        db.execute("UPDATE workflows SET definition=? WHERE id=?", (json.dumps(doc), wid))
    assert from_ip("10.1.1.1").post(url, json={}).status_code == 403


def test_the_run_button_is_not_affected(client):
    wid, _, _ = hook(client, allowed_ips="203.0.113.7")
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success"
