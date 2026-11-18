import json

import pytest

from conftest import FakeResponse, edge, make_flow, node

NOTION_DB = "a" * 32
SID = "AC" + "1" * 32


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def run(client, service, operation, fields, credential_value, name="APPCRED", extra=None):
    client.put(f"/api/credentials/{name}", json={"value": credential_value})
    app_node = node("a", "app", service=service, operation=operation, credential=name, fields=json.dumps(fields), **(extra or {}))
    wid = make_flow(client, [node("t", "trigger"), app_node, node("o", "output")], [edge("t", "a"), edge("a", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


def test_slack_posts_with_a_bot_token(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"ok": True, "channel": "C1", "ts": "1.2"})
    res = run(client, "slack", "post_message", {"channel": "#general", "text": "hi"}, "xoxb-test-token")
    assert res["status"] == "success" and res["output"] == {"ok": True, "channel": "C1", "ts": "1.2"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://slack.com/api/chat.postMessage" and call["headers"]["Authorization"] == "Bearer xoxb-test-token"
    assert call["json"] == {"channel": "#general", "text": "hi"}
    assert "xoxb-test-token" not in json.dumps(res)


def test_slack_error_reply_fails_the_node(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"ok": False, "error": "channel_not_found"})
    res = run(client, "slack", "post_message", {"channel": "#nope", "text": "hi"}, "xoxb-test-token")
    assert res["status"] == "failed" and "channel_not_found" in res["error"]


def test_slack_rejects_a_wrong_credential_type(client, fake_http):
    res = run(client, "slack", "post_message", {"channel": "#general", "text": "hi"}, "not-a-slack-token")
    assert res["status"] == "failed" and "xoxb-" in res["error"] and not fake_http.calls


def test_notion_create_page(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "p1", "url": "https://notion.so/p1"})
    res = run(client, "notion", "create_page", {"database_id": NOTION_DB, "title": "Task", "properties": {"Status": {"select": {"name": "Todo"}}}}, "secret_notion_token")
    assert res["output"] == {"id": "p1", "url": "https://notion.so/p1"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.notion.com/v1/pages" and call["headers"]["Notion-Version"] == "2022-06-28"
    assert call["json"]["parent"] == {"database_id": NOTION_DB}
    assert call["json"]["properties"]["Name"]["title"][0]["text"]["content"] == "Task" and call["json"]["properties"]["Status"] == {"select": {"name": "Todo"}}


def test_notion_query_returns_titles(client, fake_http):
    page = {"id": "p1", "url": "u", "properties": {"Name": {"type": "title", "title": [{"plain_text": "Hello "}, {"plain_text": "world"}]}, "Tag": {"type": "select"}}}
    fake_http.reply["response"] = FakeResponse(200, {"results": [page]})
    res = run(client, "notion", "query_database", {"database_id": NOTION_DB, "limit": 5}, "secret_notion_token")
    assert res["output"] == [{"id": "p1", "url": "u", "title": "Hello world"}]
    assert fake_http.calls[-1]["json"] == {"page_size": 5}


def test_notion_rejects_a_bad_database_id(client, fake_http):
    res = run(client, "notion", "query_database", {"database_id": "../../x"}, "secret_notion_token")
    assert res["status"] == "failed" and not fake_http.calls


def test_airtable_list_and_create(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"records": [{"id": "rec1", "fields": {"Name": "A"}}]})
    res = run(client, "airtable", "list_records", {"base_id": "appABCDEFGHIJKL", "table": "My Table"}, "pat_secret_value")
    assert res["output"] == [{"_id": "rec1", "Name": "A"}]
    assert fake_http.calls[-1]["url"] == "https://api.airtable.com/v0/appABCDEFGHIJKL/My%20Table?maxRecords=20"
    fake_http.reply["response"] = FakeResponse(200, {"id": "rec2", "fields": {"Name": "B"}})
    res = run(client, "airtable", "create_record", {"base_id": "appABCDEFGHIJKL", "table": "Tasks", "record": {"Name": "B"}}, "pat_secret_value")
    assert res["output"] == {"_id": "rec2", "Name": "B"} and fake_http.calls[-1]["json"] == {"fields": {"Name": "B"}}


def test_airtable_blocks_path_tricks(client, fake_http):
    res = run(client, "airtable", "list_records", {"base_id": "appABCDEFGHIJKL", "table": "../meta"}, "pat_secret_value")
    assert res["status"] == "failed" and not fake_http.calls


def test_stripe_operations(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"data": [{"id": "cus_1", "email": "a@b.co", "name": "A", "extra": 1}]})
    res = run(client, "stripe", "list_customers", {"limit": 3}, "sk_test_abcdef")
    assert res["output"] == [{"id": "cus_1", "email": "a@b.co", "name": "A"}]
    assert fake_http.calls[-1]["url"] == "https://api.stripe.com/v1/customers?limit=3"
    fake_http.reply["response"] = FakeResponse(200, {"id": "cus_2", "email": "x@y.zz", "name": None})
    res = run(client, "stripe", "create_customer", {"email": "x@y.zz"}, "sk_test_abcdef")
    assert res["output"]["id"] == "cus_2" and fake_http.calls[-1]["data"] == {"email": "x@y.zz"}
    fake_http.reply["response"] = FakeResponse(200, {"data": [{"id": "pi_1", "amount": 500, "currency": "usd", "status": "succeeded", "secret": "no"}]})
    assert run(client, "stripe", "list_payments", {}, "sk_test_abcdef")["output"] == [{"id": "pi_1", "amount": 500, "currency": "usd", "status": "succeeded"}]


def test_stripe_publishable_key_is_refused(client, fake_http):
    res = run(client, "stripe", "list_customers", {}, "pk_test_abcdef")
    assert res["status"] == "failed" and "sk_" in res["error"]


def test_twilio_sms(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"sid": "SM1", "status": "queued"})
    token = "t" * 32
    res = run(client, "twilio", "send_sms", {"to": "+15551230000", "from": "+15559990000", "body": "Hello"}, f"{SID}:{token}")
    assert res["output"] == {"sid": "SM1", "status": "queued"}
    call = fake_http.calls[-1]
    assert call["url"] == f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json" and call["auth"] == (SID, token)
    assert call["data"] == {"To": "+15551230000", "From": "+15559990000", "Body": "Hello"}
    assert token not in json.dumps(res)


def test_twilio_validates_numbers_and_credential(client, fake_http):
    assert run(client, "twilio", "send_sms", {"to": "call me", "from": "+1555", "body": "x"}, f"{SID}:{'t' * 32}")["status"] == "failed"
    assert run(client, "twilio", "send_sms", {"to": "+15551230000", "from": "+15559990000", "body": "x"}, "not-a-twilio-credential", name="TWBAD")["status"] == "failed"
    assert not fake_http.calls


def test_linear_create_issue(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"data": {"issueCreate": {"success": True, "issue": {"id": "i1", "identifier": "ENG-1", "url": "u"}}}})
    team = "b" * 8 + "-" + "b" * 4 + "-" + "b" * 4 + "-" + "b" * 4 + "-" + "b" * 12
    res = run(client, "linear", "create_issue", {"team_id": team, "title": "Bug", "description": "details"}, "lin_api_secretvalue")
    assert res["output"]["identifier"] == "ENG-1"
    call = fake_http.calls[-1]
    assert call["headers"]["Authorization"] == "lin_api_secretvalue" and call["json"]["variables"]["input"]["title"] == "Bug"


def test_linear_graphql_errors_fail_the_node(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"errors": [{"message": "Not authorized"}]})
    res = run(client, "linear", "create_issue", {"team_id": "c" * 36, "title": "Bug"}, "lin_api_secretvalue")
    assert res["status"] == "failed" and "Not authorized" in res["error"]


def test_hubspot_contacts(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"id": "9", "properties": {"email": "a@b.co", "firstname": "A"}})
    res = run(client, "hubspot", "create_contact", {"email": "a@b.co", "firstname": "A", "ignored": "x"}, "pat-na1-secret")
    assert res["output"] == {"id": "9", "email": "a@b.co", "firstname": "A"}
    assert fake_http.calls[-1]["json"] == {"properties": {"email": "a@b.co", "firstname": "A"}}
    fake_http.reply["response"] = FakeResponse(200, {"results": [{"id": "9", "properties": {"email": "a@b.co"}}]})
    listed = run(client, "hubspot", "list_contacts", {}, "pat-na1-secret")["output"]
    assert listed[0]["id"] == "9" and listed[0]["email"] == "a@b.co" and listed[0]["phone"] is None


def test_hubspot_needs_an_email(client, fake_http):
    assert run(client, "hubspot", "create_contact", {"firstname": "A"}, "pat-na1-secret")["status"] == "failed"


def test_unknown_service_and_operation(client, fake_http):
    assert "Choose a service" in run(client, "nope", "x", {}, "v")["error"]
    assert "Unknown slack action" in run(client, "slack", "delete_everything", {}, "xoxb-test-token")["error"]


def test_fields_accept_expressions_from_incoming_data(client, fake_http):
    client.put("/api/credentials/SL", json={"value": "xoxb-test-token"})
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    nodes = [node("t", "trigger"), node("i", "input", json='{"who": "ada \\"the\\" great", "n": 4}'),
             node("a", "app", service="slack", operation="post_message", credential="SL", fields='{"channel": "#g", "text": "{{who}} has {{ n * 2 }}"}'), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "a"), edge("a", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success"
    assert fake_http.calls[-1]["json"]["text"] == 'ada "the" great has 8'


def test_app_node_runs_for_each_item(client, fake_http):
    client.put("/api/credentials/SL", json={"value": "xoxb-test-token"})
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    nodes = [node("t", "trigger"), node("i", "input", json='[{"n": "a"}, {"n": "b"}]'),
             node("a", "app", service="slack", operation="post_message", credential="SL", each_item="true", fields='{"channel": "#g", "text": "hello {{n}}"}'), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "a"), edge("a", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "success"
    assert [c["json"]["text"] for c in fake_http.calls[-2:]] == ["hello a", "hello b"]


def test_http_failure_does_not_leak_the_credential(client, fake_http):
    fake_http.reply["response"] = FakeResponse(401, {"error": "bad"})
    res = run(client, "stripe", "list_customers", {}, "sk_test_supersecretvalue")
    assert res["status"] == "failed" and "supersecretvalue" not in json.dumps(res)
