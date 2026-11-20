import json

import pytest

from app import main
from conftest import FakeResponse, edge, make_flow, node
from test_poll import activate, runs, tick


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM poll_seen")
        db.execute("DELETE FROM poll_state")


def app_poll(client, service="stripe", operation="list_customers", fields="{}", cred="STRIPEKEY", secret="sk_test_poll_value_1", **cfg):
    client.put(f"/api/credentials/{cred}", json={"value": secret})
    data = {"source": "app", "service": service, "operation": operation, "fields": fields, "credential": cred, "minutes": "5", **cfg}
    return make_flow(client, [node("p", "poll_in", **data), node("o", "output")], [edge("p", "o")])


def customers(*ids):
    return FakeResponse(200, {"data": [{"id": f"cus_{i}", "email": f"{i}@x.io", "name": f"N{i}"} for i in ids]})


def test_new_stripe_customers_start_runs_and_old_ones_do_not(client, fake_http):
    fake_http.reply["response"] = customers(2, 1)  # Stripe lists newest first
    wid = app_poll(client)
    activate(client, wid)
    tick(wid)
    assert runs(wid) == []
    fake_http.reply["response"] = customers(4, 3, 2, 1)
    tick(wid)
    got = runs(wid)
    assert [json.loads(r["output"])["id"] for r in got] == ["cus_3", "cus_4"] and all(r["status"] == "success" for r in got)
    assert fake_http.calls[-1]["url"].startswith("https://api.stripe.com/v1/customers") and fake_http.calls[-1]["headers"]["Authorization"] == "Bearer sk_test_poll_value_1"
    tick(wid)
    assert len(runs(wid)) == 2


def test_airtable_records_use_the_underscore_id(client, fake_http):
    base = json.dumps({"base_id": "appABCDEFGHIJKL", "table": "Tasks"})
    fake_http.reply["response"] = FakeResponse(200, {"records": [{"id": "recAAAAAAAAAAAAA1", "fields": {"A": 1}}]})
    wid = app_poll(client, "airtable", "list_records", base, cred="ATKEY", secret="airtable-token-value", order="oldest_first")
    activate(client, wid)
    tick(wid)
    fake_http.reply["response"] = FakeResponse(200, {"records": [{"id": "recAAAAAAAAAAAAA1", "fields": {"A": 1}}, {"id": "recBBBBBBBBBBBBB2", "fields": {"A": 2}}]})
    tick(wid)
    assert [json.loads(r["output"]) for r in runs(wid)] == [{"_id": "recBBBBBBBBBBBBB2", "A": 2}]


def test_an_explicit_id_field_and_items_without_one(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"data": [{"email": "a@x.io", "id": None}]})
    wid = app_poll(client, id_path="email")
    activate(client, wid)
    tick(wid)
    fake_http.reply["response"] = FakeResponse(200, {"data": [{"email": "a@x.io", "id": None}, {"email": "b@x.io", "id": None}]})
    tick(wid)
    assert [json.loads(r["output"])["email"] for r in runs(wid)] == ["b@x.io"]


def test_a_service_error_is_recorded_as_a_failed_run_without_the_key(client, fake_http):
    fake_http.reply["response"] = FakeResponse(401, {"error": "bad key"})
    wid = app_poll(client, secret="sk_test_leak_check_value")
    activate(client, wid)
    tick(wid)
    got = runs(wid)
    assert len(got) == 1 and got[0]["status"] == "failed" and "sk_test_leak_check_value" not in json.dumps(got[0])


def test_only_list_actions_can_be_watched(client):
    for service, op, fields in (("stripe", "create_customer", "{}"), ("slack", "post_message", "{}"), ("nope", "list", "{}"), ("stripe", "list_customers", "[1]"), ("stripe", "list_customers", "not json")):
        data = {"source": "app", "service": service, "operation": op, "fields": fields, "minutes": "5"}
        wid = make_flow(client, [node("p", "poll_in", **data), node("o", "output")], [edge("p", "o")])
        res = client.post(f"/api/workflows/{wid}/active", json={"active": True})
        assert res.status_code == 400, (service, op, fields)


def test_every_watchable_action_exists():
    for service, ops in main.POLL_APP_OPS.items():
        assert service in main.APP_OPS and all(op in main.APP_OPS[service] for op in ops), service
