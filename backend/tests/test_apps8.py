import pytest

from conftest import FakeResponse
from test_apps import run


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def ok(res): assert res["status"] == "success", res.get("error"); return res["output"]


def failed(res): assert res["status"] == "failed"; return res["error"]


TS = "1700000000.000100"


def test_slack_edit_react_and_list(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"ok": True, "channel": "C1", "ts": TS})
    assert ok(run(client, "slack", "update_message", {"channel": "C1", "ts": TS, "text": "new"}, "xoxb-test-token"))["ts"] == TS
    call = fake_http.calls[-1]
    assert call["url"] == "https://slack.com/api/chat.update" and call["json"] == {"channel": "C1", "ts": TS, "text": "new"}
    ok(run(client, "slack", "add_reaction", {"channel": "C1", "ts": TS, "name": ":thumbsup:"}, "xoxb-test-token"))
    call = fake_http.calls[-1]
    assert call["url"] == "https://slack.com/api/reactions.add" and call["json"] == {"channel": "C1", "timestamp": TS, "name": "thumbsup"}
    fake_http.reply["response"] = FakeResponse(200, {"ok": True, "channels": [{"id": "C1", "name": "general", "num_members": 4, "x": 1}]})
    assert ok(run(client, "slack", "list_channels", {"limit": 5}, "xoxb-test-token")) == [{"id": "C1", "name": "general", "members": 4}]
    assert fake_http.calls[-1]["params"]["limit"] == 5
    for op, bad in (("update_message", {"ts": "abc", "text": "x"}), ("add_reaction", {"ts": TS, "name": "Bad Name"}), ("update_message", {"ts": TS})):
        assert failed(run(client, "slack", op, {"channel": "C1", **bad}, "xoxb-test-token"))
    fake_http.reply["response"] = FakeResponse(200, {"ok": False, "error": "message_not_found"})
    assert "message_not_found" in failed(run(client, "slack", "update_message", {"channel": "C1", "ts": TS, "text": "x"}, "xoxb-test-token"))


PAGE = "0123456789abcdef0123456789abcdef"


def test_notion_get_and_update_page(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": PAGE, "url": "https://notion.so/p", "archived": False, "properties": {"a": 1}})
    assert ok(run(client, "notion", "get_page", {"page_id": PAGE}, "secret_notion_token"))["id"] == PAGE
    assert fake_http.calls[-1]["method"] == "GET" and fake_http.calls[-1]["url"] == f"https://api.notion.com/v1/pages/{PAGE}"
    ok(run(client, "notion", "update_page", {"page_id": PAGE, "properties": {"Status": {"select": {"name": "Done"}}}, "archived": "true"}, "secret_notion_token"))
    call = fake_http.calls[-1]
    assert call["method"] == "PATCH" and call["json"] == {"properties": {"Status": {"select": {"name": "Done"}}}, "archived": True}
    for bad in ({"page_id": "../x"}, {"page_id": PAGE}):
        assert failed(run(client, "notion", "update_page", bad, "secret_notion_token"))


def test_airtable_get_and_delete(client, fake_http):
    base = {"base_id": "appABCDEFGHIJKL", "table": "Tasks", "record_id": "recABCDEFGHIJKLMN"}
    fake_http.reply["response"] = FakeResponse(200, {"id": "recABCDEFGHIJKLMN", "fields": {"A": 1}})
    assert ok(run(client, "airtable", "get_record", base, "airtable-token-value")) == {"_id": "recABCDEFGHIJKLMN", "A": 1}
    assert fake_http.calls[-1]["method"] == "GET" and fake_http.calls[-1]["url"].endswith("/Tasks/recABCDEFGHIJKLMN")
    fake_http.reply["response"] = FakeResponse(200, {"deleted": True, "id": "recABCDEFGHIJKLMN"})
    assert ok(run(client, "airtable", "delete_record", base, "airtable-token-value")) == {"deleted": True, "id": "recABCDEFGHIJKLMN"}
    assert fake_http.calls[-1]["method"] == "DELETE"
    n = len(fake_http.calls)
    assert failed(run(client, "airtable", "delete_record", {**base, "record_id": "../x"}, "airtable-token-value"))
    assert len(fake_http.calls) == n


def test_stripe_customer_refund_invoices(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "cus_ABC123", "email": "a@b.io", "name": "A", "balance": 0, "currency": "usd", "created": 1, "secret": "no"})
    out = ok(run(client, "stripe", "get_customer", {"customer_id": "cus_ABC123"}, "sk_test_value_1234"))
    assert out["email"] == "a@b.io" and "secret" not in out
    fake_http.reply["response"] = FakeResponse(200, {"id": "re_1", "amount": 500, "currency": "usd", "status": "succeeded"})
    assert ok(run(client, "stripe", "create_refund", {"payment_intent": "pi_ABC123", "amount": "500"}, "sk_test_value_1234"))["status"] == "succeeded"
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.stripe.com/v1/refunds" and call["data"] == {"payment_intent": "pi_ABC123", "amount": "500"}
    ok(run(client, "stripe", "create_refund", {"payment_intent": "pi_ABC123"}, "sk_test_value_1234"))
    assert fake_http.calls[-1]["data"] == {"payment_intent": "pi_ABC123"}
    n = len(fake_http.calls)
    for op, bad in (("create_refund", {"payment_intent": "pi_ABC123", "amount": "-5"}), ("create_refund", {"payment_intent": "x"}), ("create_refund", {"payment_intent": "pi_ABC123", "amount": "1.5"}), ("get_customer", {"customer_id": "../x"})):
        assert failed(run(client, "stripe", op, bad, "sk_test_value_1234"))
    assert len(fake_http.calls) == n
    fake_http.reply["response"] = FakeResponse(200, {"data": [{"id": "in_1", "customer": "cus_1", "amount_due": 100, "currency": "usd", "status": "open", "x": 1}]})
    assert ok(run(client, "stripe", "list_invoices", {}, "sk_test_value_1234")) == [{"id": "in_1", "customer": "cus_1", "amount_due": 100, "currency": "usd", "status": "open"}]


def test_hubspot_update_contact_and_create_deal(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "5", "properties": {"phone": "1"}})
    assert ok(run(client, "hubspot", "update_contact", {"contact_id": "5", "phone": "1"}, "hubspot-token-value"))["id"] == "5"
    call = fake_http.calls[-1]
    assert call["method"] == "PATCH" and call["url"].endswith("/contacts/5") and call["json"] == {"properties": {"phone": "1"}}
    ok(run(client, "hubspot", "create_deal", {"dealname": "Big", "amount": "1000.50", "dealstage": "appointmentscheduled"}, "hubspot-token-value"))
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.hubapi.com/crm/v3/objects/deals" and call["json"] == {"properties": {"dealname": "Big", "amount": "1000.50", "dealstage": "appointmentscheduled"}}
    for op, bad in (("update_contact", {"contact_id": "x", "phone": "1"}), ("update_contact", {"contact_id": "5"}), ("create_deal", {"dealname": "d", "amount": "lots"}), ("create_deal", {})):
        assert failed(run(client, "hubspot", op, bad, "hubspot-token-value"))


def test_trello_comment_and_todoist_list_and_close(client, fake_http):
    card = "a" * 24
    fake_http.reply["response"] = FakeResponse(200, {"id": "act1"})
    assert ok(run(client, "trello", "add_comment", {"card_id": card, "text": "Hi"}, "key:token")) == {"id": "act1"}
    call = fake_http.calls[-1]
    assert call["url"] == f"https://api.trello.com/1/cards/{card}/actions/comments" and call["params"]["text"] == "Hi"
    assert failed(run(client, "trello", "add_comment", {"card_id": "x", "text": "Hi"}, "key:token"))
    fake_http.reply["response"] = FakeResponse(200, [{"id": "1", "content": "Buy", "due": {"string": "tomorrow"}, "url": "u", "x": 1}, {"id": "2", "content": "Sell"}])
    assert ok(run(client, "todoist", "list_tasks", {"limit": 1}, "todoist-token-value")) == [{"id": "1", "content": "Buy", "due": "tomorrow", "url": "u"}]
    fake_http.reply["response"] = FakeResponse(204, None, "")
    assert ok(run(client, "todoist", "close_task", {"task_id": "123"}, "todoist-token-value")) == {"closed": True, "id": "123"}
    assert fake_http.calls[-1]["url"] == "https://api.todoist.com/rest/v2/tasks/123/close"
    assert failed(run(client, "todoist", "close_task", {"task_id": "../x"}, "todoist-token-value"))
