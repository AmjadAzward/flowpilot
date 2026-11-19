import base64
import json

import pytest

from conftest import FakeResponse
from test_apps import run


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def ok(res): assert res["status"] == "success", res.get("error"); return res["output"]


def failed(res): assert res["status"] == "failed"; return res["error"]


def basic(user, pw): return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def test_teams_and_google_chat_only_accept_their_own_webhook_hosts(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"name": "spaces/x/messages/y"})
    assert ok(run(client, "msteams", "post_message", {"text": "Hi"}, "https://contoso.webhook.office.com/webhookb2/abc")) == {"sent": True}
    assert fake_http.calls[-1]["url"] == "https://contoso.webhook.office.com/webhookb2/abc" and fake_http.calls[-1]["json"] == {"text": "Hi"}
    assert ok(run(client, "googlechat", "post_message", {"text": "Hi"}, "https://chat.googleapis.com/v1/spaces/S/messages?key=k&token=t")) == {"name": "spaces/x/messages/y"}
    n = len(fake_http.calls)
    for svc, cred in (("msteams", "https://evil.example.com/hook"), ("msteams", "https://webhook.office.com.evil.io/x"), ("googlechat", "https://example.com/v1")):
        assert failed(run(client, svc, "post_message", {"text": "Hi"}, cred))
    assert len(fake_http.calls) == n


def test_datadog_and_segment(client, fake_http):
    fake_http.reply["response"] = FakeResponse(202, {"event": {"id": 7, "url": "https://app.datadoghq.com/e/7"}})
    assert ok(run(client, "datadog", "create_event", {"title": "Deploy", "text": "v2", "alert_type": "success", "tags": ["env:prod"], "site": "datadoghq.eu"}, "dd-key-value-123")) == {"id": 7, "url": "https://app.datadoghq.com/e/7"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.datadoghq.eu/api/v1/events" and call["headers"] == {"DD-API-KEY": "dd-key-value-123"} and call["json"]["tags"] == ["env:prod"]
    for bad in ({"site": "evil.com"}, {"alert_type": "bad"}, {"tags": "x"}):
        assert failed(run(client, "datadog", "create_event", {"title": "t", "text": "x", **bad}, "dd-key-value-123"))
    fake_http.reply["response"] = FakeResponse(200, {"success": True})
    assert ok(run(client, "segment", "track", {"event": "Signed Up", "user_id": "u1", "properties": {"plan": "free"}}, "segment-write-key")) == {"success": True}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.segment.io/v1/track" and call["headers"]["Authorization"] == basic("segment-write-key", "") and call["json"]["userId"] == "u1"


def test_webex_pushbullet_messagebird_telnyx(client, fake_http):
    room = "Y2lzY29zcGFyazovL3VzL1JPT00vYWJjZGVm"
    fake_http.reply["response"] = FakeResponse(200, {"id": "m1"})
    assert ok(run(client, "webex", "post_message", {"room_id": room, "text": "Hi"}, "webex-token-value")) == {"id": "m1"}
    assert fake_http.calls[-1]["json"] == {"roomId": room, "text": "Hi"} and fake_http.calls[-1]["url"] == "https://webexapis.com/v1/messages"
    assert failed(run(client, "webex", "post_message", {"room_id": "../x", "text": "Hi"}, "webex-token-value"))
    fake_http.reply["response"] = FakeResponse(200, {"iden": "p1", "x": 1})
    assert ok(run(client, "pushbullet", "push_note", {"title": "T", "body": "B"}, "pb-token-value")) == {"iden": "p1"}
    assert fake_http.calls[-1]["headers"] == {"Access-Token": "pb-token-value"} and fake_http.calls[-1]["json"] == {"type": "note", "title": "T", "body": "B"}
    fake_http.reply["response"] = FakeResponse(201, {"id": "mb1"})
    assert ok(run(client, "messagebird", "send_sms", {"to": "+15551230000", "from": "Acme", "text": "Hi"}, "mb-key-value")) == {"id": "mb1"}
    call = fake_http.calls[-1]
    assert call["headers"] == {"Authorization": "AccessKey mb-key-value"} and call["json"] == {"originator": "Acme", "recipients": ["15551230000"], "body": "Hi"}
    assert failed(run(client, "messagebird", "send_sms", {"to": "abc", "from": "Acme", "text": "Hi"}, "mb-key-value"))
    fake_http.reply["response"] = FakeResponse(200, {"data": {"id": "tx1"}})
    assert ok(run(client, "telnyx", "send_sms", {"to": "+15551230000", "from": "+15559990000", "text": "Hi"}, "telnyx-key-value")) == {"id": "tx1"}
    assert fake_http.calls[-1]["json"] == {"from": "+15559990000", "to": "+15551230000", "text": "Hi"}
    assert failed(run(client, "telnyx", "send_sms", {"to": "15551230000", "from": "+15559990000", "text": "Hi"}, "telnyx-key-value"))


def test_mailjet_loops_buttondown(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"Messages": [{"Status": "success"}]})
    assert ok(run(client, "mailjet", "send_email", {"to": "a@b.io", "from": "Me <me@b.io>", "subject": "Hi", "text": "Yo"}, "mjkey:mjsecret")) == {"status": "success"}
    call = fake_http.calls[-1]
    assert call["headers"]["Authorization"] == basic("mjkey", "mjsecret") and call["json"]["Messages"][0]["From"] == {"Email": "me@b.io"}
    assert failed(run(client, "mailjet", "send_email", {"to": "a@b.io", "from": "me@b.io", "subject": "Hi", "text": "Yo"}, "nocolon"))
    fake_http.reply["response"] = FakeResponse(200, {"success": True, "id": "c1"})
    assert ok(run(client, "loops", "create_contact", {"email": "a@b.io", "first_name": "Ann", "user_group": "beta"}, "loops-key-value")) == {"success": True, "id": "c1"}
    assert fake_http.calls[-1]["json"] == {"email": "a@b.io", "firstName": "Ann", "userGroup": "beta"}
    assert failed(run(client, "loops", "create_contact", {"email": "nope"}, "loops-key-value"))
    fake_http.reply["response"] = FakeResponse(201, {"id": "s1", "x": 1})
    assert ok(run(client, "buttondown", "add_subscriber", {"email": "a@b.io"}, "bd-key-value")) == {"id": "s1"}
    assert fake_http.calls[-1]["headers"] == {"Authorization": "Token bd-key-value"} and fake_http.calls[-1]["json"] == {"email_address": "a@b.io"}


def test_baserow_hosted_and_self_hosted(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": 4})
    assert ok(run(client, "baserow", "create_row", {"table_id": "12", "row": {"Name": "x"}}, "br-token-value")) == {"id": 4}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.baserow.io/api/database/rows/table/12/?user_field_names=true" and call["headers"] == {"Authorization": "Token br-token-value"}
    ok(run(client, "baserow", "create_row", {"server": "https://db.example.com", "table_id": "12", "row": {"Name": "x"}}, "br-token-value"))
    assert fake_http.calls[-1]["url"].startswith("https://db.example.com/api/database/")
    for bad in ({"table_id": "1/../2"}, {"table_id": "12", "row": {}}, {"table_id": "12", "row": "x"}):
        assert failed(run(client, "baserow", "create_row", {"row": {"a": 1}, **bad}, "br-token-value"))


def test_healthchecks_and_uptime_kuma_need_no_credential(client, fake_http):
    cid = "01234567-89ab-cdef-0123-456789abcdef"
    fake_http.reply["response"] = FakeResponse(200, "OK")
    assert ok(run(client, "healthchecks", "ping", {"check_id": cid, "status": "fail"}, "unused-value")) == {"ok": True}
    assert fake_http.calls[-1]["url"] == f"https://hc-ping.com/{cid}/fail"
    ok(run(client, "healthchecks", "ping", {"check_id": cid}, "unused-value"))
    assert fake_http.calls[-1]["url"] == f"https://hc-ping.com/{cid}"
    for bad in ({"check_id": "x"}, {"check_id": cid, "status": "boom"}):
        assert failed(run(client, "healthchecks", "ping", bad, "unused-value"))
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    assert ok(run(client, "uptimekuma", "push", {"server": "https://kuma.example.com", "token": "abcDEF1234", "status": "down", "message": "slow"}, "unused-value")) == {"ok": True}
    call = fake_http.calls[-1]
    assert call["url"] == "https://kuma.example.com/api/push/abcDEF1234" and call["params"] == {"status": "down", "msg": "slow"}
    assert failed(run(client, "uptimekuma", "push", {"server": "https://kuma.example.com", "token": "../x"}, "unused-value"))


def test_mastodon_matrix_zulip_rocketchat(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "9", "url": "https://m.example.com/@a/9"})
    assert ok(run(client, "mastodon", "post_status", {"server": "https://m.example.com", "status": "Hello", "visibility": "public"}, "masto-token-value")) == {"id": "9", "url": "https://m.example.com/@a/9"}
    assert fake_http.calls[-1]["url"] == "https://m.example.com/api/v1/statuses" and fake_http.calls[-1]["json"] == {"status": "Hello", "visibility": "public"}
    assert failed(run(client, "mastodon", "post_status", {"server": "https://m.example.com", "status": "Hello", "visibility": "everyone"}, "masto-token-value"))
    fake_http.reply["response"] = FakeResponse(200, {"event_id": "$e1"})
    assert ok(run(client, "matrix", "send_message", {"server": "https://matrix.example.org", "room_id": "!abc123:example.org", "text": "Hi"}, "matrix-token-value")) == {"event_id": "$e1"}
    call = fake_http.calls[-1]
    assert call["method"] == "PUT" and call["url"].startswith("https://matrix.example.org/_matrix/client/v3/rooms/%21abc123%3Aexample.org/send/m.room.message/") and call["json"] == {"msgtype": "m.text", "body": "Hi"}
    assert failed(run(client, "matrix", "send_message", {"server": "https://matrix.example.org", "room_id": "#alias:example.org", "text": "Hi"}, "matrix-token-value"))
    fake_http.reply["response"] = FakeResponse(200, {"id": 55})
    assert ok(run(client, "zulip", "send_message", {"server": "https://z.example.com", "to": "general", "topic": "news", "content": "Hi"}, "bot@z.example.com:zulipkey")) == {"id": 55}
    call = fake_http.calls[-1]
    assert call["data"] == {"type": "stream", "to": "general", "content": "Hi", "topic": "news"} and call["headers"]["Authorization"] == basic("bot@z.example.com", "zulipkey")
    assert failed(run(client, "zulip", "send_message", {"server": "https://z.example.com", "to": "general", "content": "Hi"}, "bot@z.example.com:zulipkey"))  # topic missing
    fake_http.reply["response"] = FakeResponse(200, {"success": True})
    assert ok(run(client, "rocketchat", "post_message", {"server": "https://rc.example.com", "channel": "#general", "text": "Hi"}, "uid123:authtoken456")) == {"ok": True}
    call = fake_http.calls[-1]
    assert call["headers"] == {"X-User-Id": "uid123", "X-Auth-Token": "authtoken456"} and call["url"] == "https://rc.example.com/api/v1/chat.postMessage"
    assert failed(run(client, "rocketchat", "post_message", {"server": "https://rc.example.com", "channel": "general", "text": "Hi"}, "uid123:authtoken456"))


def test_ifttt(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, "Congratulations! You've fired the x event")
    assert "Congratulations" in ok(run(client, "ifttt", "trigger", {"event": "doorbell", "value1": "front"}, "iftttkey/abc"))["message"]
    call = fake_http.calls[-1]
    assert call["url"] == "https://maker.ifttt.com/trigger/doorbell/with/key/iftttkey%2Fabc" and call["json"] == {"value1": "front"}
    assert failed(run(client, "ifttt", "trigger", {"event": "a/b"}, "iftttkey"))


def test_private_addresses_are_refused_for_new_self_hosted_services(client):
    # No fake network: the real address check must refuse before anything is sent.
    cases = (("mastodon", "post_status", {"status": "x"}), ("matrix", "send_message", {"room_id": "!a:b.io", "text": "x"}), ("zulip", "send_message", {"to": "g", "topic": "t", "content": "x"}),
             ("rocketchat", "post_message", {"channel": "#g", "text": "x"}), ("uptimekuma", "push", {"token": "abcDEF1234"}), ("baserow", "create_row", {"table_id": "1", "row": {"a": 1}}))
    cred = {"zulip": "b@z.io:key", "rocketchat": "u:t", "uptimekuma": "unused-value"}
    for svc, op, extra in cases:
        for server in ("https://localhost", "https://169.254.169.254", "http://x.example.com"):
            assert failed(run(client, svc, op, {"server": server, **extra}, cred.get(svc, "selfhost-token-value"), name="PRIV" + svc.upper()))


def test_errors_never_leak_the_credential(client, fake_http):
    secret = "leaktest_secret_value_99"
    fake_http.reply["response"] = FakeResponse(500, {"error": "no"})
    for svc, op, f in (("pushbullet", "push_note", {"body": "x"}), ("loops", "create_contact", {"email": "a@b.io"}), ("buttondown", "add_subscriber", {"email": "a@b.io"}), ("ifttt", "trigger", {"event": "e"})):
        err = failed(run(client, svc, op, f, secret, name="LEAK" + svc.upper()))
        assert secret not in json.dumps(err)


def test_healthchecks_works_with_no_credential_selected(client, fake_http):
    from conftest import edge, make_flow, node
    fake_http.reply["response"] = FakeResponse(200, "OK")
    n = node("a", "app", service="healthchecks", operation="ping", credential="", fields=json.dumps({"check_id": "01234567-89ab-cdef-0123-456789abcdef"}))
    wid = make_flow(client, [node("t", "trigger"), n, node("o", "output")], [edge("t", "a"), edge("a", "o")])
    assert ok(client.post(f"/api/workflows/{wid}/run").json()) == {"ok": True}
