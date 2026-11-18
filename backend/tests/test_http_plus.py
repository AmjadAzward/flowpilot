import base64
import json

import pytest

from app import main
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db: db.execute("DELETE FROM credentials")


def http(client, **cfg):
    data = {"url": "https://api.example.com/items", "method": "GET", **cfg}
    nodes = [node("t", "trigger"), node("i", "input", json='{"id": 7, "name": "Ada", "page_size": 25}'), node("h", "http", **data), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "h"), edge("h", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


# ---------------------------------------------------------------- query, form body, timeout
def test_query_parameters_are_sent_and_templated(client, fake_http):
    res = http(client, query='{"q": "{{name}}", "limit": {{page_size}}, "flag": true, "gone": null}')
    assert res["status"] == "success"
    assert fake_http.calls[-1]["params"] == {"q": "Ada", "limit": "25", "flag": "true", "gone": ""}


def test_query_values_cannot_break_out_of_the_json(client, fake_http):
    nodes = [node("t", "trigger"), node("i", "input", json='{"term": "a\\" , \\"evil\\": \\"1"}'), node("h", "http", url="https://api.example.com/s", method="GET", query='{"q": "{{term}}"}'), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "h"), edge("h", "o")])
    client.post(f"/api/workflows/{wid}/run")
    assert list(fake_http.calls[-1]["params"].keys()) == ["q"]


@pytest.mark.parametrize("bad", ['["a"]', '"text"', "{not json", json.dumps({f"k{i}": i for i in range(31)})])
def test_bad_query_settings_fail_clearly(client, fake_http, bad):
    assert http(client, query=bad)["status"] == "failed"


def test_form_body_is_urlencoded(client, fake_http):
    res = http(client, method="POST", body_type="form", body='{"name": "{{name}}", "n": {{id}}, "tags": ["a", "b"], "none": null}')
    assert res["status"] == "success"
    call = fake_http.calls[-1]
    assert call["data"] == {"name": "Ada", "n": "7", "tags": '["a", "b"]', "none": ""} and "json" not in call and "content" not in call


def test_a_form_body_must_be_an_object(client, fake_http):
    res = http(client, method="POST", body_type="form", body='["not", "an", "object"]')
    assert res["status"] == "failed" and "JSON object" in res["error"]


def test_the_default_body_type_still_sends_json(client, fake_http):
    http(client, method="POST", body='{"name": "{{name}}"}')
    assert fake_http.calls[-1]["json"] == {"name": "Ada"}


def test_timeout_is_applied_and_limited(client, fake_http):
    http(client, timeout="30")
    assert fake_http.inits[-1]["timeout"] == 30.0
    http(client, timeout="500")
    assert fake_http.inits[-1]["timeout"] == 60.0
    http(client, timeout="0")
    assert fake_http.inits[-1]["timeout"] == 8.0  # empty or zero means the default
    assert http(client, timeout="soon")["status"] == "failed"


# ---------------------------------------------------------------- pagination
class Pages:
    def __init__(self, monkeypatch, pages):
        self.pages, self.calls = list(pages), []
        monkeypatch.setattr(main, "do_request", self.fake)
        monkeypatch.setattr(main, "safe_url", lambda u: u)

    def fake(self, method, url, headers, **kw):
        self.calls.append({"url": url, "params": kw.get("params"), "headers": headers})
        return self.pages.pop(0)


def test_next_url_pagination_collects_every_page(client, monkeypatch):
    p = Pages(monkeypatch, [{"data": [1, 2], "links": {"next": "https://api.example.com/items?page=2"}}, {"data": [3], "links": {"next": "/items?page=3"}}, {"data": [4, 5], "links": {"next": None}}])
    res = http(client, paginate="next_url", items_path="data", next_path="links.next", query='{"limit": 2}')
    assert res["status"] == "success" and res["output"] == [1, 2, 3, 4, 5]
    assert [c["url"] for c in p.calls] == ["https://api.example.com/items", "https://api.example.com/items?page=2", "https://api.example.com/items?page=3"]
    assert p.calls[0]["params"] == {"limit": "2"} and p.calls[1]["params"] is None  # the next address carries its own query


def test_page_number_pagination_stops_on_an_empty_page(client, monkeypatch):
    p = Pages(monkeypatch, [[{"a": 1}], [{"a": 2}], []])
    res = http(client, paginate="page", page_param="p", first_page="0", query='{"size": 1}')
    assert res["output"] == [{"a": 1}, {"a": 2}]
    assert [c["params"] for c in p.calls] == [{"size": "1", "p": "0"}, {"size": "1", "p": "1"}, {"size": "1", "p": "2"}]


def test_the_page_limit_is_respected(client, monkeypatch):
    p = Pages(monkeypatch, [[1]] * 30)
    assert http(client, paginate="page", max_pages="3")["output"] == [1, 1, 1] and len(p.calls) == 3
    p2 = Pages(monkeypatch, [[1]] * 30)
    http(client, paginate="page", max_pages="999")
    assert len(p2.calls) == 20  # never more than 20 pages


def test_a_next_page_on_another_site_is_not_followed(client, monkeypatch):
    Pages(monkeypatch, [{"items": [1], "next": "https://evil.example.net/steal"}])
    res = http(client, paginate="next_url", items_path="items", next_path="next")
    assert res["status"] == "failed" and "different site" in res["error"]


def test_credentials_are_never_sent_to_a_different_host(client, monkeypatch):
    client.put("/api/credentials/APIKEY", json={"value": "bearer-secret-token-value"})
    p = Pages(monkeypatch, [{"items": [1], "next": "https://evil.example.net/steal"}])
    http(client, paginate="next_url", items_path="items", next_path="next", auth_type="bearer", auth_credential="APIKEY")
    assert len(p.calls) == 1 and p.calls[0]["headers"]["Authorization"] == "Bearer bearer-secret-token-value"


def test_pagination_setup_errors(client, monkeypatch):
    Pages(monkeypatch, [{"items": [1]}] * 3)
    assert "next address" in http(client, paginate="next_url", items_path="items")["error"]
    assert http(client, paginate="sideways")["status"] == "failed"
    assert "has no list" in http(client, paginate="page", items_path="missing")["error"]
    assert http(client, paginate="page", max_pages="many")["status"] == "failed"


def test_too_many_items_are_refused(client, monkeypatch):
    Pages(monkeypatch, [list(range(3000)), list(range(3000))])
    res = http(client, paginate="page")
    assert res["status"] == "failed" and "5000" in res["error"]


def test_pagination_works_with_each_item_mode_and_no_pagination_is_unchanged(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"ok": True})
    assert http(client)["output"] == {"ok": True}
