import json

import pytest

from conftest import FakeResponse
from test_apps import run


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def ok(res): assert res["status"] == "success", res.get("error"); return res["output"]


def failed(res): assert res["status"] == "failed"; return res["error"]


CHAN = "a" * 26


def test_airtable_update(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "recABCDEFGHIJKLMN", "fields": {"Status": "Done"}})
    out = ok(run(client, "airtable", "update_record", {"base_id": "appABCDEFGHIJKL", "table": "Tasks", "record_id": "recABCDEFGHIJKLMN", "record": {"Status": "Done"}}, "airtable-token-value"))
    assert out == {"_id": "recABCDEFGHIJKLMN", "Status": "Done"}
    call = fake_http.calls[-1]
    assert call["method"] == "PATCH" and call["url"] == "https://api.airtable.com/v0/appABCDEFGHIJKL/Tasks/recABCDEFGHIJKLMN" and call["json"] == {"fields": {"Status": "Done"}}
    for bad in ({"record_id": "../x"}, {"record_id": "recABCDEFGHIJKLMN", "record": {}}, {"record_id": ""}):
        assert failed(run(client, "airtable", "update_record", {"base_id": "appABCDEFGHIJKL", "table": "Tasks", "record": {"a": 1}, **bad}, "airtable-token-value"))


def test_mattermost(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"id": "post1", "x": 1})
    assert ok(run(client, "mattermost", "post_message", {"server": "https://chat.example.com/", "channel_id": CHAN, "message": "Hi"}, "mm-token-value-123")) == {"id": "post1"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://chat.example.com/api/v4/posts" and call["headers"]["Authorization"] == "Bearer mm-token-value-123"
    for bad in ({"server": "https://chat.example.com/admin"}, {"server": "https://chat.example.com?x=1"}, {"channel_id": "short"}):
        assert failed(run(client, "mattermost", "post_message", {"server": "https://chat.example.com", "channel_id": CHAN, "message": "Hi", **bad}, "mm-token-value-123"))


def test_self_hosted_services_refuse_private_addresses(client):
    # No fake network here: the real address check must refuse before anything is sent.
    for svc, op, extra in (("mattermost", "post_message", {"channel_id": CHAN, "message": "x"}), ("gotify", "send", {"message": "x"})):
        for server in ("https://localhost", "http://chat.example.com", "https://169.254.169.254"):
            assert failed(run(client, svc, op, {"server": server, **extra}, "selfhost-token-value", name="PRIV" + svc.upper()))


def test_gotify(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": 5})
    assert ok(run(client, "gotify", "send", {"server": "https://push.example.com", "message": "Hi", "title": "T", "priority": "7"}, "gotify-token-value")) == {"id": 5}
    call = fake_http.calls[-1]
    assert call["url"] == "https://push.example.com/message" and call["headers"] == {"X-Gotify-Key": "gotify-token-value"} and call["json"] == {"message": "Hi", "title": "T", "priority": 7}
    assert failed(run(client, "gotify", "send", {"server": "https://push.example.com", "message": "Hi", "priority": "99"}, "gotify-token-value"))


def test_cloudflare(client, fake_http):
    zone = "0123456789abcdef0123456789abcdef"
    fake_http.reply["response"] = FakeResponse(200, {"success": True, "result": {"id": "x"}})
    assert ok(run(client, "cloudflare", "purge_cache", {"zone_id": zone}, "cf-token-value-123")) == {"success": True}
    assert fake_http.calls[-1]["json"] == {"purge_everything": True} and fake_http.calls[-1]["url"] == f"https://api.cloudflare.com/client/v4/zones/{zone}/purge_cache"
    ok(run(client, "cloudflare", "purge_cache", {"zone_id": zone, "files": ["https://a.io/x.css"]}, "cf-token-value-123"))
    assert fake_http.calls[-1]["json"] == {"files": ["https://a.io/x.css"]}
    for bad in ({"zone_id": "../x"}, {"zone_id": zone, "files": ["/relative"]}, {"zone_id": zone, "files": "all"}):
        assert failed(run(client, "cloudflare", "purge_cache", bad, "cf-token-value-123"))


def test_posthog_and_mixpanel(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"status": 1})
    assert ok(run(client, "posthog", "capture", {"event": "signup", "distinct_id": "u1", "properties": {"plan": "free"}, "region": "eu"}, "phc_posthog_key_value")) == {"status": 1}
    call = fake_http.calls[-1]
    assert call["url"] == "https://eu.i.posthog.com/capture/" and call["json"] == {"api_key": "phc_posthog_key_value", "event": "signup", "distinct_id": "u1", "properties": {"plan": "free"}}
    assert failed(run(client, "posthog", "capture", {"event": "x", "distinct_id": "u", "region": "mars"}, "phc_posthog_key_value"))
    assert ok(run(client, "mixpanel", "track", {"event": "signup", "distinct_id": "u1", "properties": {"plan": "free"}}, "mixpanel-token-value")) == {"status": 1}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.mixpanel.com/track" and call["json"][0]["event"] == "signup" and call["json"][0]["properties"]["token"] == "mixpanel-token-value" and call["json"][0]["properties"]["plan"] == "free"
    assert failed(run(client, "mixpanel", "track", {"distinct_id": "u"}, "mixpanel-token-value"))


def test_vonage(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"messages": [{"status": "0", "message-id": "m1"}]})
    assert ok(run(client, "vonage", "send_sms", {"to": "+15551230000", "from": "FlowPilot", "text": "Hi"}, "vkey123:vsecret456")) == {"message_id": "m1"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://rest.nexmo.com/sms/json" and call["data"] == {"api_key": "vkey123", "api_secret": "vsecret456", "to": "15551230000", "from": "FlowPilot", "text": "Hi"}
    fake_http.reply["response"] = FakeResponse(200, {"messages": [{"status": "2", "error-text": "Missing to param"}]})
    assert "Missing to param" in failed(run(client, "vonage", "send_sms", {"to": "15551230000", "from": "Me", "text": "Hi"}, "vkey123:vsecret456", name="VON2"))
    for bad in ({"to": "call me"}, {"from": "Way too long sender"}):
        assert failed(run(client, "vonage", "send_sms", {"to": "15551230000", "from": "Me", "text": "Hi", **bad}, "vkey123:vsecret456", name="VON3"))
    assert failed(run(client, "vonage", "send_sms", {"to": "15551230000", "from": "Me", "text": "Hi"}, "nocolonhere", name="VON4"))


def test_no_credential_leaks(client, fake_http):
    fake_http.reply["response"] = FakeResponse(401, {"error": "no"})
    zone = "0123456789abcdef0123456789abcdef"
    cases = [("mattermost", "post_message", {"server": "https://c.example.com", "channel_id": CHAN, "message": "m"}, "mm_leaktest_secret_1"), ("gotify", "send", {"server": "https://c.example.com", "message": "m"}, "gt_leaktest_secret_2"),
             ("cloudflare", "purge_cache", {"zone_id": zone}, "cf_leaktest_secret_3"), ("posthog", "capture", {"event": "e", "distinct_id": "d"}, "phc_leaktest_secret_4"),
             ("mixpanel", "track", {"event": "e", "distinct_id": "d"}, "mp_leaktest_secret_5"), ("vonage", "send_sms", {"to": "15551230000", "from": "Me", "text": "t"}, "vk_leak:vn_leaktest_secret_6")]
    for svc, op, fields, secret in cases:
        res = run(client, svc, op, fields, secret, name="LEAK" + svc.upper())
        assert res["status"] == "failed" and secret.split(":")[-1] not in json.dumps(res), svc
