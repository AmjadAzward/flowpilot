import json

import pytest

from app import main
from conftest import edge, make_flow, node
from test_connectors import MANIFEST


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db: db.execute("DELETE FROM connectors")


def test_the_export_includes_connectors_and_no_secrets(client):
    client.put("/api/connectors/acme", json={"manifest": MANIFEST})
    client.put("/api/credentials/ACMEKEY", json={"value": "acme-secret-value-1"})
    body = client.get("/api/backup/workflows").json()
    assert body["connectors"]["acme"]["label"] == "Acme CRM" and "acme-secret-value-1" not in json.dumps(body)


def test_restore_brings_connectors_back_without_overwriting(client):
    client.put("/api/connectors/acme", json={"manifest": MANIFEST})
    wf = make_flow(client, [node("t", "trigger"), node("a", "app", service="connector", connector="acme", operation="get_contact", fields='{"id": "1"}')], [edge("t", "a")], name="Uses acme")
    backup = client.get("/api/backup/workflows").json()
    client.delete("/api/connectors/acme")
    res = client.post("/api/backup/restore", json=backup).json()
    assert res["connectors_created"] == ["acme"] and res["connectors_skipped"] == []
    assert client.get("/api/connectors/acme").json()["base_url"] == MANIFEST["base_url"]
    again = client.post("/api/backup/restore", json=backup).json()
    assert again["connectors_created"] == [] and again["connectors_skipped"][0]["reason"] == "already exists"
    assert wf and len(again["created"]) >= 1


def test_bad_connectors_in_a_file_are_skipped_not_trusted(client):
    bad = {"Bad Name": MANIFEST, "evil": {**MANIFEST, "base_url": "http://internal.example"}, "good": MANIFEST}
    res = client.post("/api/backup/restore", json={"format": "flowpilot-backup", "workflows": [], "connectors": bad}).json()
    assert res["connectors_created"] == ["good"] and {s["name"] for s in res["connectors_skipped"]} == {"Bad Name", "evil"}
    assert [c["name"] for c in client.get("/api/connectors").json()] == ["good"]


def test_old_backup_files_without_connectors_still_restore(client):
    res = client.post("/api/backup/restore", json={"format": "flowpilot-backup", "workflows": []})
    assert res.status_code == 200 and res.json()["connectors_created"] == []


def test_only_the_owner_restores(client):
    from fastapi.testclient import TestClient
    client.post("/api/users", json={"username": "bundle-admin", "password": "long-enough-pw", "role": "admin"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "bundle-admin", "password": "long-enough-pw"}).status_code == 200
    res = c.post("/api/backup/restore", json={"format": "flowpilot-backup", "workflows": [], "connectors": {"x1": MANIFEST}})
    assert res.status_code == 403 and client.get("/api/connectors").json() == []  # restore stays owner-only, so connectors cannot be slipped in by an admin
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'bundle-admin')}")


# ---- import from a link
PACK = {"format": "flowpilot-backup", "version": 1, "workflows": [{"name": "From a link", "description": "", "nodes": [{"id": "t", "type": "trigger", "position": {"x": 0, "y": 0}, "data": {"label": "t"}}], "edges": []}], "connectors": {"linked": MANIFEST}}


def test_a_pack_can_be_imported_from_a_link(client, fake_http):
    from conftest import FakeResponse
    fake_http.reply["response"] = FakeResponse(200, PACK)
    res = client.post("/api/backup/import-url", json={"url": "https://raw.example.com/pack.json"})
    assert res.status_code == 200 and [c["name"] for c in res.json()["created"]] == ["From a link"] and res.json()["connectors_created"] == ["linked"]
    assert fake_http.calls[-1]["method"] == "GET" and fake_http.calls[-1]["url"] == "https://raw.example.com/pack.json"
    wf = next(w for w in client.get("/api/workflows").json() if w["name"] == "From a link")
    assert wf["active"] is False  # nothing starts by itself


def test_links_that_are_not_packs_or_not_allowed_are_refused(client, fake_http):
    from conftest import FakeResponse
    for reply in (FakeResponse(200, [1, 2]), FakeResponse(200, "plain text"), FakeResponse(200, {"format": "other", "workflows": []}), FakeResponse(200, {"hello": "world"}), FakeResponse(404, {"e": 1})):
        fake_http.reply["response"] = reply
        assert client.post("/api/backup/import-url", json={"url": "https://raw.example.com/x.json"}).status_code == 400


def test_private_and_insecure_links_are_refused_before_any_request(client):
    for url in ("http://raw.example.com/pack.json", "https://localhost/pack.json", "https://127.0.0.1/pack.json", "https://169.254.169.254/latest", "ftp://x.example.com/p"):
        assert client.post("/api/backup/import-url", json={"url": url}).status_code == 400, url


def test_only_the_owner_can_import_from_a_link(client, fake_http):
    from fastapi.testclient import TestClient
    client.post("/api/users", json={"username": "link-admin", "password": "long-enough-pw", "role": "admin"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "link-admin", "password": "long-enough-pw"}).status_code == 200
    assert c.post("/api/backup/import-url", json={"url": "https://raw.example.com/pack.json"}).status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'link-admin')}")
