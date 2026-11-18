import pytest

from app import main
from conftest import FakeResponse, edge, make_flow, node


def run_http(client, payload='{"name":"Ada","n":3,"id":42,"token":"s3cret"}', **http):
    wid = make_flow(client,
        [node("t", "trigger"), node("i", "input", json=payload), node("h", "http", **http)],
        [edge("t", "i"), edge("i", "h")])
    return client.post(f"/api/workflows/{wid}/run").json()


def test_post_sends_templated_json_body(client, fake_http):
    res = run_http(client, url="https://api.example.com/x", method="POST", body='{"who":"{{name}}","count":{{n}}}')
    assert res["status"] == "success" and res["output"] == {"ok": True}
    call = fake_http.calls[0]
    assert call["method"] == "POST" and call["json"] == {"who": "Ada", "count": 3}


def test_whole_incoming_object_can_be_sent_with_empty_path(client, fake_http):
    run_http(client, payload='{"a":1}', url="https://api.example.com/x", method="POST", body="{{}}")
    assert fake_http.calls[0]["json"] == {"a": 1}


def test_put_and_patch_send_bodies_but_get_and_delete_do_not(client, fake_http):
    for method in ("PUT", "PATCH", "GET", "DELETE"):
        run_http(client, url="https://api.example.com/x", method=method, body='{"a":1}')
    by_method = {c["method"]: c for c in fake_http.calls}
    assert by_method["PUT"]["json"] == {"a": 1} and by_method["PATCH"]["json"] == {"a": 1}
    assert "json" not in by_method["GET"] and "content" not in by_method["GET"]
    assert "json" not in by_method["DELETE"] and "content" not in by_method["DELETE"]


def test_method_is_case_insensitive_and_defaults_to_get(client, fake_http):
    run_http(client, url="https://api.example.com/x", method="post", body="{}")
    run_http(client, url="https://api.example.com/x")
    assert [c["method"] for c in fake_http.calls] == ["POST", "GET"]


def test_text_body_gets_default_content_type_but_explicit_one_wins(client, fake_http):
    run_http(client, url="https://api.example.com/x", method="POST", body="hello {{name}}")
    assert fake_http.calls[0]["content"] == b"hello Ada"
    assert fake_http.calls[0]["headers"]["Content-Type"].startswith("text/plain")
    run_http(client, url="https://api.example.com/x", method="POST", body="a=1", headers='{"content-type":"application/x-www-form-urlencoded"}')
    assert fake_http.calls[1]["headers"] == {"content-type": "application/x-www-form-urlencoded"}


def test_headers_support_templates(client, fake_http):
    run_http(client, url="https://api.example.com/x", headers='{"Authorization":"Bearer {{token}}","X-Count":3}')
    assert fake_http.calls[0]["headers"] == {"Authorization": "Bearer s3cret", "X-Count": "3"}


def test_url_supports_templates(client, fake_http):
    run_http(client, url="https://api.example.com/items/{{id}}")
    assert fake_http.calls[0]["url"] == "https://api.example.com/items/42"


def test_text_response_and_error_status(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, None, "plain text")
    assert run_http(client, url="https://api.example.com/x")["output"] == "plain text"
    fake_http.reply["response"] = FakeResponse(503, {"error": "down"})
    res = run_http(client, url="https://api.example.com/x")
    assert res["status"] == "failed" and "503" in res["error"]


def test_client_never_follows_redirects_or_trusts_env(client, fake_http):
    run_http(client, url="https://api.example.com/x")
    assert fake_http.inits[0]["follow_redirects"] is False and fake_http.inits[0]["trust_env"] is False


@pytest.mark.parametrize("headers", [
    "[1]", "{bad json", '{"Host":"evil.example"}', '{"Content-Length":"1"}', '{"Transfer-Encoding":"chunked"}',
    '{"Bad Name":"x"}', '{"X-A":"line\\nbreak"}', '{"X-A":{"nested":1}}', '{"X-A":null}',
])
def test_bad_headers_fail_the_step_without_sending(client, fake_http, headers):
    res = run_http(client, url="https://api.example.com/x", headers=headers)
    assert res["status"] == "failed" and fake_http.calls == []


def test_too_many_headers_rejected(client, fake_http):
    many = "{" + ",".join(f'"X-{i}":"v"' for i in range(21)) + "}"
    assert run_http(client, url="https://api.example.com/x", headers=many)["status"] == "failed"


def test_oversized_request_body_rejected(client, fake_http):
    res = run_http(client, url="https://api.example.com/x", method="POST", body="x" * 1_000_001)
    assert res["status"] == "failed" and "1 MB" in res["error"] and fake_http.calls == []


def test_unsupported_method_rejected(client, fake_http):
    assert run_http(client, url="https://api.example.com/x", method="CONNECT")["status"] == "failed"


def test_templated_url_cannot_smuggle_in_a_private_address(client, monkeypatch):
    """Uses the real safe_url: the host is rendered from data, then validated."""
    res = run_http(client, payload='{"h":"127.0.0.1"}', url="https://{{h}}/x")
    assert res["status"] == "failed" and "public" in res["error"].lower()
    res = run_http(client, payload='{"h":"169.254.169.254"}', url="https://{{h}}/latest")
    assert res["status"] == "failed"
