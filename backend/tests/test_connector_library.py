import json
import re
from urllib.parse import urlparse

import pytest

from app import connectors, main
from app.connector_library import LIBRARY
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db: db.execute("DELETE FROM connectors")


def test_there_are_many_unique_well_formed_entries():
    ids = [e["id"] for e in LIBRARY]
    assert len(ids) >= 20 and len(set(ids)) == len(ids)
    assert all(connectors.NAME_RE.fullmatch(i) and e["about"] for i, e in zip(ids, LIBRARY))


@pytest.mark.parametrize("entry", LIBRARY, ids=[e["id"] for e in LIBRARY])
def test_every_manifest_is_valid_and_every_example_builds(entry):
    manifest = connectors.validate_manifest(entry["manifest"])
    host = urlparse(manifest["base_url"]).hostname
    assert host and "." in host and not host.endswith((".local", ".internal")) and not re.fullmatch(r"[\d.]+", host)
    for key, op in manifest["operations"].items():
        req = connectors.build_request(manifest, key, op["example"])
        assert req["url"].startswith(manifest["base_url"] + "/") and "{" not in req["url"], (entry["id"], key)
        assert not any("{" in str(v) for v in req["params"].values()), (entry["id"], key)
        missing = [r for r in op["required"] if r not in op["example"]]
        assert not missing, (entry["id"], key, missing)


def test_the_library_is_served_and_an_entry_can_be_saved_and_used(client, fake_http):
    got = client.get("/api/connector-library").json()
    assert len(got) == len(LIBRARY)
    pick = next(e for e in got if e["id"] == "frankfurter")
    assert client.put("/api/connectors/frankfurter", json={"manifest": pick["manifest"]}).status_code == 200
    n = node("a", "app", service="connector", connector="frankfurter", operation="latest", fields='{"from": "EUR", "to": "USD", "amount": 100}')
    wid = make_flow(client, [node("t", "trigger"), n, node("o", "output")], [edge("t", "a"), edge("a", "o")])
    fake_http.reply["response"] = FakeResponse(200, {"amount": 100, "base": "EUR", "rates": {"USD": 1.1}})
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success" and res["output"]["rates"] == {"USD": 1.1}
    call = fake_http.calls[-1]
    assert call["url"] == "https://api.frankfurter.app/latest" and call["params"] == {"from": "EUR", "to": "USD", "amount": 100}
    assert "Authorization" not in call["headers"]


def test_the_library_does_not_hold_secrets():
    text = str([e["manifest"] for e in LIBRARY])
    assert not re.search(r"(sk_live|xox[bp]-|ghp_|AKIA[0-9A-Z]{12})", text)


def _use(client, key, op, fields, secret="library-secret-value-1"):
    pick = next(e for e in client.get("/api/connector-library").json() if e["id"] == key)
    assert client.put(f"/api/connectors/{key}", json={"manifest": pick["manifest"]}).status_code == 200
    client.put("/api/credentials/LIBKEY", json={"value": secret})
    n = node("a", "app", service="connector", connector=key, operation=op, credential="LIBKEY", fields=json.dumps(fields))
    wid = make_flow(client, [node("t", "trigger"), n, node("o", "output")], [edge("t", "a"), edge("a", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


def test_postmark_sends_the_token_in_its_own_header_and_a_json_body(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"MessageID": "abc", "ErrorCode": 0})
    res = _use(client, "postmark", "send", {"from": "a@example.com", "to": "b@example.com", "subject": "Hi", "text": "Body"})
    c = fake_http.calls[-1]
    assert res["status"] == "success" and c["method"] == "POST" and c["url"] == "https://api.postmarkapp.com/email"
    assert c["headers"]["X-Postmark-Server-Token"] == "library-secret-value-1" and "library-secret-value-1" not in json.dumps(res)
    assert c["json"] == {"From": "a@example.com", "To": "b@example.com", "Subject": "Hi", "TextBody": "Body", "MessageStream": "outbound"}


def test_hunter_sends_its_key_in_the_query_and_returns_the_data_part(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"data": {"domain": "stripe.com", "emails": []}, "meta": {}})
    res = _use(client, "hunter", "domain_search", {"domain": "stripe.com", "limit": 3})
    c = fake_http.calls[-1]
    assert res["output"] == {"domain": "stripe.com", "emails": []}
    assert c["params"]["domain"] == "stripe.com" and c["params"]["api_key"] == "library-secret-value-1"


def test_lemon_squeezy_keeps_its_bracketed_query_name(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"data": [{"id": "1"}]})
    res = _use(client, "lemonsqueezy", "orders", {"page_size": 5})
    c = fake_http.calls[-1]
    assert res["output"] == [{"id": "1"}] and c["params"]["page[size]"] == 5 and c["headers"]["Authorization"] == "Bearer library-secret-value-1"


def test_agify_calls_the_root_path_with_the_name_in_the_query(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"name": "michael", "age": 62, "count": 100})
    pick = next(e for e in client.get("/api/connector-library").json() if e["id"] == "agify")
    client.put("/api/connectors/agify", json={"manifest": pick["manifest"]})
    n = node("a", "app", service="connector", connector="agify", operation="guess", fields='{"name": "michael"}')
    wid = make_flow(client, [node("t", "trigger"), n, node("o", "output")], [edge("t", "a"), edge("a", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    c = fake_http.calls[-1]
    assert res["output"]["age"] == 62 and c["url"] == "https://api.agify.io/" and c["params"] == {"name": "michael"}


def test_dockerhub_tags_return_the_results_list_and_fill_the_path(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"count": 1, "results": [{"name": "latest"}]})
    pick = next(e for e in client.get("/api/connector-library").json() if e["id"] == "dockerhub")
    client.put("/api/connectors/dockerhub", json={"manifest": pick["manifest"]})
    n = node("a", "app", service="connector", connector="dockerhub", operation="tags", fields='{"namespace": "library", "repo": "nginx", "page_size": 5}')
    wid = make_flow(client, [node("t", "trigger"), n, node("o", "output")], [edge("t", "a"), edge("a", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["output"] == [{"name": "latest"}] and fake_http.calls[-1]["url"] == "https://hub.docker.com/v2/repositories/library/nginx/tags"
