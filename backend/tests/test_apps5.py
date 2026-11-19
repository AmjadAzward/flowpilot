import json

import pytest

from conftest import FakeResponse
from test_apps import run


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def ok(res): assert res["status"] == "success", res.get("error"); return res["output"]


def failed(res): assert res["status"] == "failed"; return res["error"]


def test_pagerduty(client, fake_http):
    fake_http.reply["response"] = FakeResponse(202, {"status": "success", "dedup_key": "d1"})
    assert ok(run(client, "pagerduty", "trigger_event", {"summary": "DB down", "severity": "critical", "dedup_key": "d1"}, "routingkey0123456789")) == {"status": "success", "dedup_key": "d1"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://events.pagerduty.com/v2/enqueue" and call["json"]["routing_key"] == "routingkey0123456789" and call["json"]["payload"]["severity"] == "critical" and call["json"]["payload"]["source"] == "flowpilot"
    assert failed(run(client, "pagerduty", "trigger_event", {"summary": "x", "severity": "loud"}, "routingkey0123456789"))
    assert failed(run(client, "pagerduty", "trigger_event", {}, "routingkey0123456789"))


def test_opsgenie(client, fake_http):
    fake_http.reply["response"] = FakeResponse(202, {"requestId": "r1", "result": "Request will be processed"})
    assert ok(run(client, "opsgenie", "create_alert", {"message": "Disk", "priority": "p2"}, "opsgenie-key-value"))["request_id"] == "r1"
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.opsgenie.com/v2/alerts" and call["headers"]["Authorization"] == "GenieKey opsgenie-key-value" and call["json"]["priority"] == "P2"
    ok(run(client, "opsgenie", "create_alert", {"message": "Disk", "region": "eu"}, "opsgenie-key-value"))
    assert fake_http.calls[-1]["url"] == "https://api.eu.opsgenie.com/v2/alerts"
    for bad in ({"message": "x" * 131}, {"message": "x", "priority": "P9"}, {"message": "x", "region": "mars"}):
        assert failed(run(client, "opsgenie", "create_alert", bad, "opsgenie-key-value"))


def test_intercom(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "c1", "email": "a@b.co", "extra": 1})
    assert ok(run(client, "intercom", "create_contact", {"email": "a@b.co", "name": "Ann"}, "intercom-token-value")) == {"id": "c1", "email": "a@b.co"}
    assert fake_http.calls[-1]["json"] == {"role": "user", "email": "a@b.co", "name": "Ann"}
    assert failed(run(client, "intercom", "create_contact", {"email": "nope"}, "intercom-token-value"))


def test_freshdesk(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"id": 77})
    out = ok(run(client, "freshdesk", "create_ticket", {"subdomain": "acme", "subject": "Help", "description": "It broke", "email": "c@d.io", "priority": "3"}, "fresh-api-key-value"))
    assert out == {"id": 77, "url": "https://acme.freshdesk.com/a/tickets/77"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://acme.freshdesk.com/api/v2/tickets" and call["json"]["priority"] == 3 and call["headers"]["Authorization"].startswith("Basic ")
    for bad in ({"subdomain": "evil.com/x"}, {"subdomain": "acme", "priority": "9"}, {"subdomain": "-bad"}):
        assert failed(run(client, "freshdesk", "create_ticket", {"subject": "s", "description": "d", "email": "c@d.io", **bad}, "fresh-api-key-value"))


def test_supabase(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, [{"id": 1, "email": "a@b.co"}])
    assert ok(run(client, "supabase", "insert_row", {"project": "abcdefghij", "table": "leads", "row": {"email": "a@b.co"}}, "supabase-key-value")) == [{"id": 1, "email": "a@b.co"}]
    call = fake_http.calls[-1]
    assert call["url"] == "https://abcdefghij.supabase.co/rest/v1/leads" and call["headers"]["Prefer"] == "return=representation" and call["json"] == {"email": "a@b.co"}
    fake_http.reply["response"] = FakeResponse(200, [{"id": 2}])
    assert ok(run(client, "supabase", "select_rows", {"project": "abcdefghij", "table": "leads", "limit": 5}, "supabase-key-value")) == [{"id": 2}]
    assert fake_http.calls[-1]["params"] == {"select": "*", "limit": 5}
    for bad in ({"project": "A.evil", "table": "t"}, {"project": "abcdefghij", "table": "t?select=*&x"}, {"project": "abcdefghij", "table": "t/../x"}):
        assert failed(run(client, "supabase", "select_rows", bad, "supabase-key-value"))
    assert failed(run(client, "supabase", "insert_row", {"project": "abcdefghij", "table": "leads", "row": []}, "supabase-key-value"))


def test_bitly(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"link": "https://bit.ly/x", "id": "bit.ly/x", "other": 1})
    assert ok(run(client, "bitly", "shorten", {"url": "https://example.com/a?b=1"}, "bitly-token-value")) == {"link": "https://bit.ly/x", "id": "bit.ly/x"}
    assert fake_http.calls[-1]["json"] == {"long_url": "https://example.com/a?b=1"}
    for bad in ("javascript:alert(1)", "example.com", "http://a b"):
        assert failed(run(client, "bitly", "shorten", {"url": bad}, "bitly-token-value"))


def test_convertkit(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"subscription": {"id": 9, "state": "inactive"}})
    assert ok(run(client, "convertkit", "add_subscriber", {"form_id": "123", "email": "a@b.co", "first_name": "Ann"}, "convertkit-key-value")) == {"id": 9, "state": "inactive"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.convertkit.com/v3/forms/123/subscribe" and call["json"] == {"api_key": "convertkit-key-value", "email": "a@b.co", "first_name": "Ann"}
    assert failed(run(client, "convertkit", "add_subscriber", {"form_id": "1/../x", "email": "a@b.co"}, "convertkit-key-value"))


def test_no_credential_leaks(client, fake_http):
    fake_http.reply["response"] = FakeResponse(401, {"error": "no"})
    cases = [("pagerduty", "trigger_event", {"summary": "s"}, "pd_leaktest_routing_key_1"), ("opsgenie", "create_alert", {"message": "m"}, "og_leaktest_secret_2"),
             ("intercom", "create_contact", {"email": "a@b.co"}, "ic_leaktest_secret_3"), ("freshdesk", "create_ticket", {"subdomain": "a", "subject": "s", "description": "d", "email": "a@b.co"}, "fd_leaktest_secret_4"),
             ("supabase", "select_rows", {"project": "abcdefghij", "table": "t"}, "sb_leaktest_secret_5"), ("bitly", "shorten", {"url": "https://a.io"}, "bl_leaktest_secret_6"),
             ("convertkit", "add_subscriber", {"form_id": "1", "email": "a@b.co"}, "ck_leaktest_secret_7")]
    for svc, op, fields, secret in cases:
        res = run(client, svc, op, fields, secret, name="LEAK" + svc.upper())
        assert res["status"] == "failed" and secret not in json.dumps(res), svc
