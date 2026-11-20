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
