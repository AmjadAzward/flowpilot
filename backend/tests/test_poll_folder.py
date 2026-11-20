import json
import os

import pytest

from app import main
from conftest import FakeResponse, edge, make_flow, node
from test_connectors import MANIFEST
from test_poll import activate, runs, tick


@pytest.fixture(autouse=True)
def _clean(clean_state, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_FILES_DIR", str(tmp_path))
    yield
    with main.conn() as db:
        for t in ("poll_seen", "poll_state", "connectors"): db.execute(f"DELETE FROM {t}")


def folder_flow(client, **cfg):
    data = {"source": "folder", "minutes": "5", **cfg}
    return make_flow(client, [node("p", "poll_in", **data), node("o", "output")], [edge("p", "o")])


def put(base, rel, text="x", mtime=None):
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime: os.utime(path, (mtime, mtime))
    return path


def test_new_files_start_runs_and_existing_ones_do_not(client, tmp_path):
    put(tmp_path, "incoming/old.csv", mtime=1_700_000_000)
    wid = folder_flow(client, folder="incoming")
    activate(client, wid)
    tick(wid)
    assert runs(wid) == []
    put(tmp_path, "incoming/a.csv", "aaa", mtime=1_700_000_100)
    put(tmp_path, "incoming/b.csv", "bb", mtime=1_700_000_200)
    tick(wid)
    got = [json.loads(r["output"]) for r in runs(wid)]
    assert [g["name"] for g in got] == ["a.csv", "b.csv"] and got[0]["path"] == "incoming/a.csv" and got[0]["size"] == 3
    tick(wid)
    assert len(runs(wid)) == 2


def test_a_changed_file_counts_as_new_again(client, tmp_path):
    put(tmp_path, "log.txt", "one", mtime=1_700_000_000)
    wid = folder_flow(client)
    activate(client, wid)
    tick(wid)
    put(tmp_path, "log.txt", "one two", mtime=1_700_000_500)
    tick(wid)
    assert [json.loads(r["output"])["size"] for r in runs(wid)] == [7]


def test_pattern_filters_names_and_skips_folders(client, tmp_path):
    wid = folder_flow(client, pattern="*.csv")
    activate(client, wid)
    tick(wid)
    put(tmp_path, "a.csv"); put(tmp_path, "b.txt"); (tmp_path / "dir.csv").mkdir()
    tick(wid)
    assert [json.loads(r["output"])["name"] for r in runs(wid)] == ["a.csv"]


def test_a_missing_folder_is_just_empty(client, tmp_path):
    wid = folder_flow(client, folder="not-yet")
    activate(client, wid)
    tick(wid)
    put(tmp_path, "not-yet/a.txt")
    tick(wid)
    assert len(runs(wid)) == 1


@pytest.mark.parametrize("cfg", [{"folder": "../outside"}, {"folder": "a/../../b"}, {"folder": "/etc"}, {"folder": "C:/Windows"}, {"pattern": "../*"}, {"pattern": "a/b"}, {"pattern": "x" * 80}])
def test_unsafe_folders_and_patterns_cannot_be_activated(client, cfg):
    wid = folder_flow(client, **cfg)
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 400


def test_symlinks_are_not_followed(client, tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret")
    wid = folder_flow(client)
    activate(client, wid)
    tick(wid)
    try: os.symlink(outside, tmp_path / "link.txt")
    except (OSError, NotImplementedError): pytest.skip("symlinks not available")
    tick(wid)
    assert runs(wid) == []


# ---- a custom connector can be watched too
def connector_poll(client, op="get_contact", **cfg):
    client.put("/api/connectors/acme", json={"manifest": {**MANIFEST, "operations": {**MANIFEST["operations"], "list_contacts": {"label": "List", "method": "GET", "path": "/contacts", "result": "data"}}}})
    client.put("/api/credentials/ACMEKEY", json={"value": "acme-secret-value-1"})
    data = {"source": "app", "service": "connector", "connector": "acme", "operation": op, "fields": "{}", "credential": "ACMEKEY", "minutes": "5"}
    return make_flow(client, [node("p", "poll_in", **data), node("o", "output")], [edge("p", "o")])


def test_a_connector_get_action_can_be_watched(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, {"data": [{"id": 1, "name": "A"}]})
    wid = connector_poll(client, "list_contacts")
    activate(client, wid)
    tick(wid)
    fake_http.reply["response"] = FakeResponse(200, {"data": [{"id": 2, "name": "B"}, {"id": 1, "name": "A"}]})
    tick(wid)
    assert [json.loads(r["output"])["name"] for r in runs(wid)] == ["B"]
    assert fake_http.calls[-1]["url"] == "https://api.acme.example/v1/contacts" and fake_http.calls[-1]["headers"]["Authorization"] == "Bearer acme-secret-value-1"


def test_connector_watch_needs_a_known_get_action(client):
    wid = connector_poll(client, "create_contact")
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 400
    wid = connector_poll(client, "nope")
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 400
