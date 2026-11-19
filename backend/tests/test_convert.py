import json

import pytest

from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def conv(client, **fields):
    nodes = [node("t", "trigger"), node("i", "input", json=json.dumps({"body": fields.pop("body", "")})), node("c", "convert", **fields), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "c"), edge("c", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


def test_json_parse_and_stringify(client):
    res = conv(client, operation="json_parse", value="{{body}}", body='{"a": [1, 2], "b": {"c": true}}')
    assert res["status"] == "success" and res["output"] == {"a": [1, 2], "b": {"c": True}}
    assert conv(client, operation="json_parse", value="{{body}}", body="not json")["status"] == "failed"
    assert conv(client, operation="json_stringify", value='{"a": 1,  "b": "é"}')["output"] == '{"a":1,"b":"é"}'
    assert conv(client, operation="json_stringify", value="plain text")["output"] == '"plain text"'


def test_url_encode_decode(client):
    assert conv(client, operation="url_encode", value="a b&c=d/é")["output"] == "a%20b%26c%3Dd%2F%C3%A9"
    assert conv(client, operation="url_decode", value="a%20b%26c")["output"] == "a b&c"


XML = '<feed id="9"><title>Hello</title><item><n>1</n></item><item><n>2</n></item><empty/></feed>'


def test_xml_to_json(client):
    out = conv(client, operation="xml_to_json", value="{{body}}", body=XML)["output"]
    assert out == {"feed": {"@id": "9", "title": "Hello", "item": [{"n": "1"}, {"n": "2"}], "empty": ""}}
    assert conv(client, operation="xml_to_json", value="{{body}}", body="<a><b></a>")["status"] == "failed"


def test_xml_with_entities_is_refused(client):
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><x>&b;</x>'
    res = conv(client, operation="xml_to_json", value="{{body}}", body=bomb)
    assert res["status"] == "failed" and "DOCTYPE" in res["error"]


HTML = '<html><body><h1 class="title main">Big <b>News</b></h1><p>one</p><p>two <i>x</i></p><a href="/a" id="first">A</a><a href="https://b.io">B</a><img src="/p.png"><div id="price">$5</div></body></html>'


def test_html_extract(client):
    def pick(sel, attr=""): return conv(client, operation="html_extract", value="{{body}}", selector=sel, attribute=attr, body=HTML)
    assert pick("p")["output"] == ["one", "two x"]
    assert pick("h1.title")["output"] == ["Big News"]
    assert pick(".main")["output"] == ["Big News"]
    assert pick("#price")["output"] == ["$5"]
    assert pick("a", "href")["output"] == ["/a", "https://b.io"]
    assert pick("img", "src")["output"] == ["/p.png"]
    assert pick("a#first")["output"] == ["A"]
    assert pick("blink")["output"] == []
    for bad in ("", "div > p", "a[href]", "p, a"):
        assert pick(bad)["status"] == "failed", bad
    assert pick("a", "on click")["status"] == "failed"


def test_unknown_operation_and_each_item(client):
    assert conv(client, operation="nope", value="x")["status"] == "failed"
    nodes = [node("t", "trigger"), node("i", "input", json='["a b", "c d"]'), node("c", "convert", operation="url_encode", value="{{}}", each_item="true"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "c"), edge("c", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["output"] == ["a%20b", "c%20d"]
