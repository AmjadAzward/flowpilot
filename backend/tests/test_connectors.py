import json

import pytest

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def run(client, nodes, edges):
    wid = make_flow(client, nodes, edges)
    return client.post(f"/api/workflows/{wid}/run").json()


def cred(client, name, value):
    assert client.put(f"/api/credentials/{name}", json={"value": value}).status_code == 200


def notify_flow(client, **cfg):
    nodes = [node("t", "trigger"), node("i", "input", json='{"name":"Ada","n":3}'), node("s", "notify", **cfg), node("o", "output")]
    return run(client, nodes, [edge("t", "i"), edge("i", "s"), edge("s", "o")])


# ---------------------------------------------------------------- chat notifications
@pytest.mark.parametrize("service,key", [("slack", "text"), ("discord", "content"), ("generic", "text")])
def test_notify_payload_per_service(client, fake_http, service, key):
    res = notify_flow(client, service=service, url="https://hooks.example.com/abc", message="Hello {{name}}")
    assert res["status"] == "success"
    call = fake_http.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://hooks.example.com/abc" and call["json"] == {key: "Hello Ada"}


def test_notify_passes_the_original_data_along(client, fake_http):
    res = notify_flow(client, service="slack", url="https://hooks.example.com/abc", message="hi")
    assert res["output"] == {"name": "Ada", "n": 3}
    assert next(s for s in res["steps"] if s["node_id"] == "s")["output"] == {"sent": True, "service": "slack"}


def test_notify_webhook_url_can_come_from_a_credential_and_stays_hidden(client, fake_http):
    cred(client, "SLACK_URL", "https://hooks.example.com/services/T000/B000/secrettoken")
    res = notify_flow(client, service="slack", url="{{cred.SLACK_URL}}", message="x")
    assert fake_http.calls[0]["url"].endswith("secrettoken")
    assert "secrettoken" not in json.dumps(res)


@pytest.mark.parametrize("cfg", [
    {"service": "slack", "url": "https://hooks.example.com/a", "message": "  "},
    {"service": "carrier-pigeon", "url": "https://hooks.example.com/a", "message": "x"},
    {"service": "slack", "url": "https://hooks.example.com/a", "message": "x" * 4001},
])
def test_notify_validation(client, fake_http, cfg):
    assert notify_flow(client, **cfg)["status"] == "failed" and fake_http.calls == []


def test_notify_enforces_the_public_https_rule(client):
    for url in ("http://hooks.example.com/a", "https://127.0.0.1/a", "https://localhost/a"):
        res = notify_flow(client, service="slack", url=url, message="x")
        assert res["status"] == "failed", url


# ---------------------------------------------------------------- email
class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None, **kw):
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls, self.login_args, self.sent = False, None, []
        self.kind = type(self).__name__
        FakeSMTP.instances.append(self)

    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def starttls(self, context=None): self.started_tls = True
    def login(self, user, password): self.login_args = (user, password)
    def send_message(self, msg): self.sent.append(msg)


class FakeSMTP_SSL(FakeSMTP):
    pass


@pytest.fixture()
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(main.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(main.smtplib, "SMTP_SSL", FakeSMTP_SSL)
    monkeypatch.setattr(main, "assert_public_host", lambda host, relaxed=False: None)
    return FakeSMTP


def email_flow(client, **cfg):
    nodes = [node("t", "trigger"), node("i", "input", json='{"name":"Ada","order":"A-1"}'), node("e", "email", **cfg), node("o", "output")]
    return run(client, nodes, [edge("t", "i"), edge("i", "e"), edge("e", "o")])


GOOD = {"credential": "SMTP", "to": "ada@example.com", "subject": "Order {{order}}", "body": "Hi {{name}}"}


def test_email_starttls_default(client, smtp):
    cred(client, "SMTP", "smtp://bot%40example.com:p%40ss%3Aword@smtp.example.com?from=bot@example.com")
    res = email_flow(client, **GOOD)
    assert res["status"] == "success" and res["output"] == {"name": "Ada", "order": "A-1"}
    s = smtp.instances[0]
    assert (s.kind, s.host, s.port, s.timeout) == ("FakeSMTP", "smtp.example.com", 587, 10)
    assert s.started_tls and s.login_args == ("bot@example.com", "p@ss:word")
    msg = s.sent[0]
    assert msg["From"] == "bot@example.com" and msg["To"] == "ada@example.com" and msg["Subject"] == "Order A-1"
    assert msg.get_content().strip() == "Hi Ada"


def test_email_ssl_and_plain_modes(client, smtp):
    cred(client, "SMTP", "smtps://u:p@mail.example.com?from=bot@example.com")
    email_flow(client, **GOOD)
    s = smtp.instances[0]
    assert s.kind == "FakeSMTP_SSL" and s.port == 465 and not s.started_tls
    cred(client, "SMTP", "smtp://mail.example.com?from=bot@example.com&security=none")
    email_flow(client, **GOOD)
    plain = smtp.instances[1]
    assert plain.kind == "FakeSMTP" and plain.port == 25 and not plain.started_tls and plain.login_args is None


def test_email_multiple_recipients_and_templates(client, smtp):
    cred(client, "SMTP", "smtp://u:p@mail.example.com?from=bot@example.com")
    email_flow(client, **{**GOOD, "to": "a@example.com, b@example.com"})
    assert smtp.instances[0].sent[0]["To"] == "a@example.com, b@example.com"


@pytest.mark.parametrize("override", [
    {"to": ""}, {"to": "not-an-email"}, {"to": ", ".join(f"u{i}@example.com" for i in range(11))},
    {"subject": "line1\nBcc: evil@example.com"}, {"subject": "x" * 201}, {"body": "x" * 100_001}, {"credential": "MISSING"},
])
def test_email_validation(client, smtp, override):
    cred(client, "SMTP", "smtp://u:p@mail.example.com?from=bot@example.com")
    assert email_flow(client, **{**GOOD, **override})["status"] == "failed" and smtp.instances == []


@pytest.mark.parametrize("value", ["https://u:p@mail.example.com", "smtp://u:p@mail.example.com", "smtp://u:p@mail.example.com?from=bad&security=ssl",
                                   "smtp://u:p@mail.example.com?from=bot@example.com&security=weird"])
def test_email_rejects_malformed_credentials(client, smtp, value):
    cred(client, "SMTP", value)
    assert email_flow(client, **GOOD)["status"] == "failed" and smtp.instances == []


def test_email_password_never_appears_in_results(client, smtp):
    cred(client, "SMTP", "smtp://bot:supersecretpw@mail.example.com?from=bot@example.com")
    assert "supersecretpw" not in json.dumps(email_flow(client, **GOOD))


def test_email_blocks_private_hosts_unless_explicitly_allowed(client, monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(main.smtplib, "SMTP", FakeSMTP)
    cred(client, "SMTP", "smtp://u:p@127.0.0.1:1025?from=bot@example.com&security=none")
    assert email_flow(client, **GOOD)["status"] == "failed" and FakeSMTP.instances == []
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")
    assert email_flow(client, **GOOD)["status"] == "success" and len(FakeSMTP.instances) == 1


def test_private_host_override_does_not_weaken_http(client, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")
    nodes = [node("t", "trigger"), node("h", "http", url="https://127.0.0.1/x")]
    assert run(client, nodes, [edge("t", "h")])["status"] == "failed"
