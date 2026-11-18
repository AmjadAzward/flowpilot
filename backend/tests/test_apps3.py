import json

import pytest

from conftest import FakeResponse
from test_apps import run


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def ok(res): assert res["status"] == "success", res.get("error"); return res["output"]


def failed(res): assert res["status"] == "failed"; return res["error"]


def test_shopify_lists_orders_and_products(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"orders": [{"id": 1, "name": "#1001", "email": "a@b.co", "total_price": "12.50", "currency": "USD", "financial_status": "paid", "created_at": "2026-01-01", "secret": "x"}]})
    out = ok(run(client, "shopify", "list_orders", {"shop": "mystore", "status": "open", "limit": 5}, "shpat_secret_token_value"))
    assert out == [{"id": 1, "name": "#1001", "email": "a@b.co", "total_price": "12.50", "currency": "USD", "financial_status": "paid", "created_at": "2026-01-01"}]
    call = fake_http.calls[-1]
    assert call["url"] == "https://mystore.myshopify.com/admin/api/2024-10/orders.json" and call["headers"] == {"X-Shopify-Access-Token": "shpat_secret_token_value"} and call["params"] == {"limit": 5, "status": "open"}
    fake_http.reply["response"] = FakeResponse(200, {"products": [{"id": 9, "title": "Mug", "status": "active", "vendor": "Acme", "variants": []}]})
    assert ok(run(client, "shopify", "list_products", {"shop": "mystore"}, "shpat_secret_token_value")) == [{"id": 9, "title": "Mug", "status": "active", "vendor": "Acme"}]


def test_shopify_validates(client, fake_http):
    for bad in ({"shop": "evil.com/x"}, {"shop": "My Store"}, {"shop": "mystore", "status": "weird"}, {}):
        assert failed(run(client, "shopify", "list_orders", bad, "shpat_secret_token_value"))
    assert not fake_http.calls


def test_zendesk_ticket(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"ticket": {"id": 55}})
    out = ok(run(client, "zendesk", "create_ticket", {"subdomain": "acme", "subject": "Help", "body": "It broke", "requester_email": "c@d.io"}, "agent@acme.com:zen-api-token-123"))
    assert out == {"id": 55, "url": "https://acme.zendesk.com/agent/tickets/55"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://acme.zendesk.com/api/v2/tickets.json" and call["auth"] == ("agent@acme.com/token", "zen-api-token-123")
    assert call["json"] == {"ticket": {"subject": "Help", "comment": {"body": "It broke"}, "requester": {"email": "c@d.io"}}}
    assert failed(run(client, "zendesk", "create_ticket", {"subdomain": "a.b", "subject": "s", "body": "b"}, "agent@acme.com:zen-api-token-123"))
    assert failed(run(client, "zendesk", "create_ticket", {"subdomain": "acme", "subject": "s", "body": "b"}, "no-email-token-value", name="ZENBAD"))


def test_pipedrive_person_and_deal(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"data": {"id": 3, "name": "Ada"}})
    assert ok(run(client, "pipedrive", "create_person", {"name": "Ada", "email": "a@b.co", "phone": "123"}, "pipedrive-token-value")) == {"id": 3, "name": "Ada"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.pipedrive.com/v1/persons" and call["headers"] == {"x-api-token": "pipedrive-token-value"} and call["json"] == {"name": "Ada", "email": ["a@b.co"], "phone": ["123"]}
    fake_http.reply["response"] = FakeResponse(201, {"data": {"id": 4, "title": "Big deal"}})
    assert ok(run(client, "pipedrive", "create_deal", {"title": "Big deal", "value": "1500.5", "currency": "usd", "person_id": "3"}, "pipedrive-token-value")) == {"id": 4, "name": "Big deal"}
    assert fake_http.calls[-1]["json"] == {"title": "Big deal", "value": 1500.5, "person_id": 3, "currency": "USD"}
    assert failed(run(client, "pipedrive", "create_deal", {"title": "x", "value": "lots"}, "pipedrive-token-value"))


def test_clickup_task(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "abc", "url": "https://app.clickup.com/t/abc"})
    assert ok(run(client, "clickup", "create_task", {"list_id": "9001", "name": "Do it", "priority": "2"}, "pk_clickup_token_value")) == {"id": "abc", "url": "https://app.clickup.com/t/abc"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.clickup.com/api/v2/list/9001/task" and call["headers"] == {"Authorization": "pk_clickup_token_value"} and call["json"]["priority"] == 2
    assert failed(run(client, "clickup", "create_task", {"list_id": "../x", "name": "n"}, "pk_clickup_token_value"))
    assert failed(run(client, "clickup", "create_task", {"list_id": "9001", "name": "n", "priority": "9"}, "pk_clickup_token_value"))


def test_monday_item_and_errors(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"data": {"create_item": {"id": "77"}}})
    assert ok(run(client, "monday", "create_item", {"board_id": "123", "item_name": "Task", "column_values": {"status": {"label": "Done"}}}, "monday-token-value")) == {"id": "77"}
    sent = fake_http.calls[-1]["json"]
    assert sent["variables"] == {"board": "123", "name": "Task", "cols": '{"status": {"label": "Done"}}'} and fake_http.calls[-1]["headers"] == {"Authorization": "monday-token-value"}
    fake_http.reply["response"] = FakeResponse(200, {"errors": [{"message": "Board not found"}]})
    assert "Board not found" in failed(run(client, "monday", "create_item", {"board_id": "123", "item_name": "x"}, "monday-token-value"))
    assert failed(run(client, "monday", "create_item", {"board_id": "abc", "item_name": "x"}, "monday-token-value"))


def test_dropbox_folders(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"entries": [{".tag": "file", "name": "a.txt", "path_display": "/Docs/a.txt", "size": 5, "id": "x"}, {".tag": "folder", "name": "Sub", "path_display": "/Docs/Sub"}]})
    assert ok(run(client, "dropbox", "list_folder", {"path": "/Docs", "limit": 10}, "dropbox-token-value")) == [{"name": "a.txt", "type": "file", "path": "/Docs/a.txt", "size": 5}, {"name": "Sub", "type": "folder", "path": "/Docs/Sub", "size": None}]
    assert fake_http.calls[-1]["json"] == {"path": "/Docs", "limit": 10}
    ok(run(client, "dropbox", "list_folder", {}, "dropbox-token-value"))
    assert fake_http.calls[-1]["json"]["path"] == ""  # blank means the root
    fake_http.reply["response"] = FakeResponse(200, {"metadata": {"path_display": "/Reports"}})
    assert ok(run(client, "dropbox", "create_folder", {"path": "/Reports"}, "dropbox-token-value")) == {"path": "/Reports"}
    for bad in ("Reports", "/a/../b", "/a/./b"):
        assert failed(run(client, "dropbox", "create_folder", {"path": bad}, "dropbox-token-value")), bad


def test_resend_and_postmark_email(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "re_1"})
    assert ok(run(client, "resend", "send_email", {"to": "a@b.co", "from": "Me <me@c.io>", "subject": "Hi", "text": "Body"}, "re_resend_key_value")) == {"id": "re_1"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.resend.com/emails" and call["headers"]["Authorization"] == "Bearer re_resend_key_value" and call["json"] == {"from": "Me <me@c.io>", "to": ["a@b.co"], "subject": "Hi", "text": "Body"}
    fake_http.reply["response"] = FakeResponse(200, {"MessageID": "pm-1"})
    assert ok(run(client, "postmark", "send_email", {"to": "a@b.co", "from": "me@c.io", "subject": "Hi", "text": "Body"}, "postmark-token-value")) == {"message_id": "pm-1"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.postmarkapp.com/email" and call["headers"]["X-Postmark-Server-Token"] == "postmark-token-value" and call["json"]["TextBody"] == "Body"
    for svc, key in (("resend", "re_resend_key_value"), ("postmark", "postmark-token-value")):
        assert failed(run(client, svc, "send_email", {"to": "nope", "from": "me@c.io", "subject": "s", "text": "t"}, key))
        assert failed(run(client, svc, "send_email", {"to": "a@b.co", "from": "me@c.io", "subject": "two\nlines", "text": "t"}, key))


def test_whatsapp_text(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"messages": [{"id": "wamid.1"}]})
    assert ok(run(client, "whatsapp", "send_text", {"phone_number_id": "1234567890", "to": "+15551230000", "text": "Hello"}, "whatsapp-token-value")) == {"message_id": "wamid.1"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://graph.facebook.com/v19.0/1234567890/messages" and call["json"]["to"] == "15551230000" and call["json"]["text"] == {"body": "Hello"}
    assert failed(run(client, "whatsapp", "send_text", {"phone_number_id": "abc", "to": "15551230000", "text": "x"}, "whatsapp-token-value"))
    assert failed(run(client, "whatsapp", "send_text", {"phone_number_id": "123", "to": "call me", "text": "x"}, "whatsapp-token-value"))


def test_pushover(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"status": 1, "request": "req-1"})
    assert ok(run(client, "pushover", "send", {"message": "Disk full", "title": "Server", "priority": "1"}, "apptoken123:userkey456")) == {"status": 1, "request": "req-1"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.pushover.net/1/messages.json" and call["data"] == {"token": "apptoken123", "user": "userkey456", "message": "Disk full", "title": "Server", "priority": "1"}
    assert failed(run(client, "pushover", "send", {"message": "x"}, "no-colon-here-value", name="POBAD"))
    assert failed(run(client, "pushover", "send", {"message": "x", "priority": "2"}, "apptoken123:userkey456"))


def test_none_of_the_new_services_leak_their_credentials_on_http_errors(client, fake_http):
    fake_http.reply["response"] = FakeResponse(401, {"error": "no"})
    cases = [("shopify", "list_products", {"shop": "s"}, "shpat_leaktest_secret_1"), ("clickup", "create_task", {"list_id": "1", "name": "n"}, "pk_leaktest_secret_2"),
             ("dropbox", "list_folder", {}, "dropbox_leaktest_secret_3"), ("resend", "send_email", {"to": "a@b.co", "from": "m@c.io", "subject": "s", "text": "t"}, "re_leaktest_secret_4"),
             ("zendesk", "create_ticket", {"subdomain": "a", "subject": "s", "body": "b"}, "agent@acme.com:zen_leaktest_secret_5")]
    for svc, op, fields, secret in cases:
        res = run(client, svc, op, fields, secret, name="LEAK" + svc.upper())
        assert res["status"] == "failed" and secret.split(":")[-1] not in json.dumps(res), svc
