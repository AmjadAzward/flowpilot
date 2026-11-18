import json

import pytest

from conftest import FakeResponse, edge, make_flow, node
from test_apps import run


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def test_sendgrid_sends_plain_text_mail(client, fake_http):
    fake_http.reply["response"] = FakeResponse(202, None, text="")
    res = run(client, "sendgrid", "send_email", {"to": "a@b.co", "from": "me@c.io", "subject": "Hi", "text": "Body"}, "SG.secret-key-value")
    assert res["status"] == "success" and res["output"] == {"sent": True, "to": "a@b.co"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.sendgrid.com/v3/mail/send" and call["headers"]["Authorization"] == "Bearer SG.secret-key-value"
    assert call["json"]["personalizations"] == [{"to": [{"email": "a@b.co"}]}] and call["json"]["content"][0]["value"] == "Body"
    assert run(client, "sendgrid", "send_email", {"to": "nope", "from": "me@c.io", "subject": "Hi", "text": "x"}, "SG.secret-key-value")["status"] == "failed"
    assert run(client, "sendgrid", "send_email", {"to": "a@b.co", "from": "me@c.io", "subject": "two\nlines", "text": "x"}, "SG.secret-key-value")["status"] == "failed"


def test_mailchimp_uses_the_data_centre_from_the_key(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "abc", "email_address": "a@b.co", "status": "subscribed"})
    key = "0123456789abcdef0123456789abcdef-us21"
    res = run(client, "mailchimp", "add_subscriber", {"list_id": "a1b2c3d4e5", "email": "a@b.co", "first_name": "Ada"}, key)
    assert res["output"] == {"id": "abc", "email": "a@b.co", "status": "subscribed"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://us21.api.mailchimp.com/3.0/lists/a1b2c3d4e5/members" and call["auth"] == ("flowpilot", key)
    assert call["json"] == {"email_address": "a@b.co", "status": "subscribed", "merge_fields": {"FNAME": "Ada"}}
    assert key not in json.dumps(res)
    assert run(client, "mailchimp", "add_subscriber", {"list_id": "a1b2c3d4e5", "email": "a@b.co", "status": "weird"}, key)["status"] == "failed"
    assert run(client, "mailchimp", "add_subscriber", {"list_id": "../x", "email": "a@b.co"}, key)["status"] == "failed"
    assert run(client, "mailchimp", "add_subscriber", {"list_id": "a1b2c3d4e5", "email": "a@b.co"}, "no-datacentre-key-1234", name="MCBAD")["status"] == "failed"


def test_trello_card_keeps_key_and_token_out_of_results(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "c1", "shortUrl": "https://trello.com/c/x"})
    res = run(client, "trello", "create_card", {"list_id": "a" * 24, "name": "Card", "description": "d"}, "trellokey123:trellotoken456")
    assert res["output"] == {"id": "c1", "url": "https://trello.com/c/x"}
    assert fake_http.calls[-1]["params"]["idList"] == "a" * 24 and fake_http.calls[-1]["params"]["key"] == "trellokey123"
    assert "trellotoken456" not in json.dumps(res)
    assert run(client, "trello", "create_card", {"list_id": "short", "name": "x"}, "trellokey123:trellotoken456")["status"] == "failed"


def test_trello_http_failure_does_not_leak_the_token(client, fake_http):
    fake_http.reply["response"] = FakeResponse(401, {"error": "bad"})
    res = run(client, "trello", "create_card", {"list_id": "a" * 24, "name": "Card"}, "trellokey123:trellotoken456")
    assert res["status"] == "failed" and "trellotoken456" not in json.dumps(res) and "trellokey123" not in json.dumps(res)


def test_asana_task(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"data": {"gid": "77", "permalink_url": "https://app.asana.com/0/1/77"}})
    res = run(client, "asana", "create_task", {"name": "Do it", "notes": "n", "project_id": "12345"}, "asana-pat-secret")
    assert res["output"] == {"id": "77", "url": "https://app.asana.com/0/1/77"}
    assert fake_http.calls[-1]["json"] == {"data": {"name": "Do it", "notes": "n", "projects": ["12345"]}}
    assert run(client, "asana", "create_task", {"name": "x", "project_id": "abc"}, "asana-pat-secret")["status"] == "failed"


def test_jira_issue(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"id": "10001", "key": "ENG-7"})
    res = run(client, "jira", "create_issue", {"site": "mycompany", "project": "ENG", "summary": "Broken", "description": "Details", "type": "Bug"}, "me@example.com:jira-api-token")
    assert res["output"] == {"key": "ENG-7", "id": "10001", "url": "https://mycompany.atlassian.net/browse/ENG-7"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://mycompany.atlassian.net/rest/api/3/issue" and call["auth"] == ("me@example.com", "jira-api-token")
    assert call["json"]["fields"]["issuetype"] == {"name": "Bug"} and call["json"]["fields"]["description"]["type"] == "doc"
    assert "jira-api-token" not in json.dumps(res)
    assert run(client, "jira", "create_issue", {"site": "evil.com/x", "project": "ENG", "summary": "s"}, "me@example.com:jira-api-token")["status"] == "failed"
    assert run(client, "jira", "create_issue", {"site": "mycompany", "project": "ENG", "summary": "s"}, "just-a-token-no-email", name="JIRABAD")["status"] == "failed"


def test_todoist_task(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "9", "url": "https://todoist.com/showTask?id=9"})
    res = run(client, "todoist", "create_task", {"content": "Buy milk", "due": "tomorrow at 9am"}, "todoist-token-value")
    assert res["output"]["id"] == "9" and fake_http.calls[-1]["json"]["due_string"] == "tomorrow at 9am"


def test_gitlab_issue_encodes_the_project_path(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"iid": 3, "web_url": "https://gitlab.com/g/p/-/issues/3"})
    res = run(client, "gitlab", "create_issue", {"project": "group/sub/proj", "title": "T", "description": "D"}, "glpat-secret-value")
    assert res["output"] == {"iid": 3, "url": "https://gitlab.com/g/p/-/issues/3"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://gitlab.com/api/v4/projects/group%2Fsub%2Fproj/issues" and call["headers"] == {"PRIVATE-TOKEN": "glpat-secret-value"}
    assert run(client, "gitlab", "create_issue", {"project": "../../admin", "title": "T"}, "glpat-secret-value")["status"] == "failed"


def test_discord_bot_message(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "555"})
    res = run(client, "discord", "post_message", {"channel_id": "123456789012345678", "content": "Hello"}, "discord-bot-token-value")
    assert res["output"] == {"id": "555"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://discord.com/api/v10/channels/123456789012345678/messages" and call["headers"]["Authorization"] == "Bot discord-bot-token-value"
    assert run(client, "discord", "post_message", {"channel_id": "abc", "content": "x"}, "discord-bot-token-value")["status"] == "failed"
    assert run(client, "discord", "post_message", {"channel_id": "123456", "content": "x" * 2001}, "discord-bot-token-value")["status"] == "failed"


def test_ntfy_needs_no_credential(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "x"})
    fields = '{"topic": "my-alerts", "message": "Disk full", "title": "Server", "priority": "4"}'
    wid = make_flow(client, [node("t", "trigger"), node("a", "app", service="ntfy", operation="publish", credential="", fields=fields), node("o", "output")], [edge("t", "a"), edge("a", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success" and res["output"] == {"sent": True, "topic": "my-alerts"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://ntfy.sh/my-alerts" and call["content"] == b"Disk full" and call["headers"] == {"Title": "Server", "Priority": "4"}


def test_ntfy_with_a_token_and_validation(client, fake_http):
    res = run(client, "ntfy", "publish", {"topic": "private", "message": "x"}, "tk_ntfy_secret_token")
    assert res["status"] == "success" and fake_http.calls[-1]["headers"]["Authorization"] == "Bearer tk_ntfy_secret_token"
    for bad in ({"topic": "a/b", "message": "x"}, {"topic": "ok", "message": ""}, {"topic": "ok", "message": "x", "priority": "9"}, {"topic": "ok", "message": "x", "title": "a\nb"}):
        assert run(client, "ntfy", "publish", bad, "tk_ntfy_secret_token")["status"] == "failed", bad


def test_other_services_still_require_a_credential(client, fake_http):
    fields = '{"channel_id": "123456", "content": "x"}'
    wid = make_flow(client, [node("t", "trigger"), node("a", "app", service="discord", operation="post_message", credential="", fields=fields), node("o", "output")], [edge("t", "a"), edge("a", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["status"] == "failed" and not fake_http.calls
