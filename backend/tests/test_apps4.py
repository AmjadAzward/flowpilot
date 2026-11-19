import json

import pytest

from conftest import FakeResponse
from test_apps import run


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def ok(res): assert res["status"] == "success", res.get("error"); return res["output"]


def failed(res): assert res["status"] == "failed"; return res["error"]


MAIL = {"to": "a@b.co", "from": "Me <me@c.io>", "subject": "Hi", "text": "Body"}


def test_brevo(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"messageId": "<m1>"})
    assert ok(run(client, "brevo", "send_email", MAIL, "xkeysib-brevo-secret")) == {"message_id": "<m1>"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.brevo.com/v3/smtp/email" and call["headers"]["api-key"] == "xkeysib-brevo-secret"
    assert call["json"]["sender"] == {"email": "me@c.io", "name": "Me"} and call["json"]["to"] == [{"email": "a@b.co"}]
    assert failed(run(client, "brevo", "send_email", {**MAIL, "to": "nope"}, "xkeysib-brevo-secret"))


def test_mailgun(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"id": "<id>", "message": "Queued"})
    assert ok(run(client, "mailgun", "send_email", {**MAIL, "domain": "mg.example.com"}, "mailgun-secret-key")) == {"id": "<id>", "message": "Queued"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.mailgun.net/v3/mg.example.com/messages" and call["headers"]["Authorization"].startswith("Basic ")
    ok(run(client, "mailgun", "send_email", {**MAIL, "domain": "mg.example.com", "region": "eu"}, "mailgun-secret-key"))
    assert fake_http.calls[-1]["url"].startswith("https://api.eu.mailgun.net/")
    for bad in ({"domain": "evil.com/x"}, {"domain": "mg.example.com", "region": "asia"}, {}):
        assert failed(run(client, "mailgun", "send_email", {**MAIL, **bad}, "mailgun-secret-key"))


def test_telegram(client, fake_http):
    token = "123456789:AAH_telegram-token-value-0123456789"
    fake_http.reply["response"] = FakeResponse(200, {"ok": True, "result": {"message_id": 7}})
    assert ok(run(client, "telegram", "send_message", {"chat_id": "-100123", "text": "Hello"}, token)) == {"message_id": 7}
    assert fake_http.calls[-1]["url"] == f"https://api.telegram.org/bot{token}/sendMessage"
    assert ok(run(client, "telegram", "send_message", {"chat_id": "@mychannel", "text": "Hello"}, token, name="TG2")) == {"message_id": 7}
    assert failed(run(client, "telegram", "send_message", {"chat_id": "../x", "text": "Hello"}, token, name="TG3"))
    assert failed(run(client, "telegram", "send_message", {"chat_id": "1", "text": "Hello"}, "not-a-bot-token-value", name="TG4"))


def test_github(client, fake_http):
    fake_http.reply["response"] = FakeResponse(201, {"number": 12, "html_url": "https://github.com/o/r/issues/12"})
    out = ok(run(client, "github", "create_issue", {"repo": "o/r", "title": "Bug", "body": "x", "labels": ["bug"]}, "ghp_secret_token_value"))
    assert out == {"number": 12, "url": "https://github.com/o/r/issues/12"}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.github.com/repos/o/r/issues" and call["json"]["labels"] == ["bug"] and call["headers"]["Authorization"] == "Bearer ghp_secret_token_value"
    fake_http.reply["response"] = FakeResponse(201, {"id": 5, "html_url": "https://github.com/o/r/issues/12#c5"})
    assert ok(run(client, "github", "add_comment", {"repo": "o/r", "number": "12", "body": "thanks"}, "ghp_secret_token_value"))["id"] == 5
    assert fake_http.calls[-1]["url"] == "https://api.github.com/repos/o/r/issues/12/comments"
    for op, bad in (("create_issue", {"repo": "../../admin", "title": "t"}), ("create_issue", {"repo": "o/r", "title": "t", "labels": "bug"}), ("add_comment", {"repo": "o/r", "number": "0", "body": "b"}), ("add_comment", {"repo": "o/r", "number": "1/../x", "body": "b"})):
        assert failed(run(client, "github", op, bad, "ghp_secret_token_value"))


def test_new_services_do_not_leak_credentials(client, fake_http):
    fake_http.reply["response"] = FakeResponse(401, {"error": "no"})
    cases = [("telegram", "send_message", {"chat_id": "1", "text": "t"}, "123456789:AAH_leaktest-token-value-0123456"), ("github", "create_issue", {"repo": "o/r", "title": "t"}, "ghp_leaktest_secret_1"),
             ("brevo", "send_email", MAIL, "xkeysib-leaktest-2"), ("mailgun", "send_email", {**MAIL, "domain": "mg.example.com"}, "mailgun-leaktest-3")]
    for svc, op, fields, secret in cases:
        res = run(client, svc, op, fields, secret, name="LEAK" + svc.upper())
        assert res["status"] == "failed" and secret not in json.dumps(res), svc
