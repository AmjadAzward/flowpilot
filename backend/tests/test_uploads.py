import json
import os
import time

import pytest

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_FILES_DIR", str(tmp_path))
    yield


def hook(client, accept=True, form_fields=None, extra_nodes=None, extra_edges=None):
    data = {"sample": "{}"}
    if accept: data["accept_files"] = "true"
    if form_fields is not None: data["form_fields"] = json.dumps(form_fields)
    nodes = [node("w", "webhook", **data), node("o", "output")] + (extra_nodes or [])
    wid = make_flow(client, nodes, [edge("w", "o")] + (extra_edges or []))
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    return wid, f"/api/hooks/{token}"


def last_run(wid):
    with main.conn() as db: return dict(db.execute("SELECT * FROM executions WHERE workflow_id=? ORDER BY started_at DESC LIMIT 1", (wid,)).fetchone())


def received(wid): return json.loads(last_run(wid)["output"])["body"]


def test_a_file_and_fields_arrive_and_are_stored(client, anon, tmp_path):
    wid, url = hook(client)
    payload = b"%PDF-1.4 binary \x00\x01\xff data"
    res = anon.post(url, data={"name": "Ada", "note": "hello"}, files={"document": ("cv.pdf", payload, "application/pdf")})
    assert res.status_code == 200, res.text
    body = received(wid)
    assert body["name"] == "Ada" and body["note"] == "hello" and len(body["files"]) == 1
    f = body["files"][0]
    assert f["field"] == "document" and f["filename"] == "cv.pdf" and f["content_type"] == "application/pdf" and f["size"] == len(payload)
    assert f["path"].startswith("uploads/") and f["path"].endswith("-cv.pdf")
    assert (tmp_path / f["path"]).read_bytes() == b"%PDF-1.4 binary \x00\x01\xff data"  # byte for byte, binary safe


def test_several_files_and_empty_file_inputs(client, anon):
    wid, url = hook(client)
    anon.post(url, files=[("a", ("one.txt", b"1", "text/plain")), ("b", ("two.txt", b"22", "text/plain")), ("c", ("", b"", "application/octet-stream"))])
    files = received(wid)["files"]
    assert [(f["field"], f["size"]) for f in files] == [("a", 1), ("b", 2)]  # the empty "no file chosen" input is ignored


def test_filenames_are_made_safe(client, anon, tmp_path):
    wid, url = hook(client)
    anon.post(url, files=[("f", ("../../etc/passwd", b"x", "text/plain")), ("g", ("..\\..\\win dows\\evil name!.exe", b"y", "text/plain")), ("h", ("...", b"z", "text/plain"))])
    files = received(wid)["files"]
    for f in files:
        resolved = (tmp_path / f["path"]).resolve()
        assert (tmp_path / "uploads").resolve() in resolved.parents and resolved.read_bytes() in (b"x", b"y", b"z")
        assert ".." not in f["path"].split("/") and " " not in f["path"] and "!" not in f["path"] and "\\" not in f["path"]
    assert files[0]["path"].endswith("-passwd") and files[1]["path"].endswith("-evil_name_.exe") and files[2]["path"].endswith("-file")
    assert not (tmp_path.parent / "etc").exists()


def test_uploads_must_be_switched_on(client, anon, tmp_path):
    wid, url = hook(client, accept=False)
    res = anon.post(url, files={"document": ("cv.pdf", b"data", "application/pdf")})
    assert res.status_code == 415 and not (tmp_path / "uploads").exists()


def test_limits(client, anon, tmp_path):
    wid, url = hook(client)
    six = [(f"f{i}", (f"{i}.txt", b"x", "text/plain")) for i in range(6)]
    assert anon.post(url, files=six).status_code == 413
    big = b"x" * (5_000_001)
    assert anon.post(url, files={"big": ("big.bin", big, "application/octet-stream")}).status_code == 413
    huge = b"x" * 10_100_000
    assert anon.post(url, files={"huge": ("huge.bin", huge, "application/octet-stream")}).status_code == 413
    ok = anon.post(url, files={"fits": ("fits.bin", b"x" * 4_000_000, "application/octet-stream")})
    assert ok.status_code == 200
    stored = [p for p in (tmp_path / "uploads").rglob("*") if p.is_file()]
    assert len(stored) == 1  # refused uploads leave nothing behind


def test_a_refused_upload_does_not_run_the_workflow(client, anon):
    wid, url = hook(client)
    anon.post(url, files=[(f"f{i}", (f"{i}.txt", b"x", "text/plain")) for i in range(6)])
    with main.conn() as db: assert db.execute("SELECT COUNT(*) c FROM executions WHERE workflow_id=?", (wid,)).fetchone()["c"] == 0


def test_malformed_multipart_is_a_clean_error(client, anon):
    wid, url = hook(client)
    res = anon.post(url, content=b"this is not multipart at all", headers={"content-type": "multipart/form-data; boundary=zzz"})
    assert res.status_code in (200, 400)  # nothing parsed means no fields and no files, never a crash
    assert anon.post(url, content=b"--zzz\r\nbroken", headers={"content-type": "multipart/form-data"}).status_code in (200, 400)


def test_a_form_with_a_file_field_works_without_the_switch(client, anon):
    fields = [{"name": "email", "label": "Email", "type": "email"}, {"name": "cv", "label": "Your CV", "type": "file"}]
    wid, url = hook(client, accept=False, form_fields=fields)
    page = anon.get(url)
    assert "enctype='multipart/form-data'" in page.text and "type='file'" in page.text and "name='cv'" in page.text
    res = anon.post(url, data={"email": "a@b.co", "unexpected": "dropped"}, files={"cv": ("cv.txt", b"my cv", "text/plain")}, headers={"accept": "text/html"})
    assert res.status_code == 200 and "Thank you" in res.text
    body = received(wid)
    assert body["email"] == "a@b.co" and "unexpected" not in body and body["files"][0]["filename"] == "cv.txt"


def test_a_plain_form_does_not_get_the_multipart_encoding(client, anon):
    wid, url = hook(client, accept=False, form_fields=[{"name": "email", "label": "Email", "type": "email"}])
    assert "enctype" not in anon.get(url).text


def test_the_files_node_can_read_an_upload_back(client, anon):
    nodes = [node("r", "file", operation="read", path="{{body.files.0.path}}", **{"as": "text"})]
    wid, url = hook(client, extra_nodes=nodes, extra_edges=[edge("w", "r")])
    anon.post(url, files={"doc": ("note.txt", b"hello upload", "text/plain")})
    steps = json.loads(last_run(wid)["steps"])
    read = next(s for s in steps if s["node_id"] == "r")
    assert read["status"] == "success" and "hello upload" in json.dumps(read["output"])


def test_json_and_urlencoded_webhooks_are_unchanged(client, anon):
    wid, url = hook(client, accept=False)
    assert anon.post(url, json={"a": 1}).status_code == 200 and received(wid) == {"a": 1}
    assert anon.post(url, data={"b": "2"}).status_code == 200 and received(wid) == {"b": "2"}
    assert anon.post(url, content=b"x" * (300 * 1024)).status_code == 413  # the small limit still applies without uploads


def test_the_body_limit_does_not_depend_on_the_content_length_header(client, anon):
    wid, url = hook(client, accept=False)
    def chunks():
        for _ in range(40): yield b"x" * 10_000
    assert anon.post(url, content=chunks()).status_code == 413  # a streamed body with no length is still refused


def test_old_uploads_are_pruned(client, tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    for day in ("20200101", "20990101", "notadate"):
        (root / day).mkdir(parents=True)
        (root / day / "f.txt").write_text("x")
    assert main.prune_uploads(30) == 1
    assert not (root / "20200101").exists() and (root / "20990101").exists() and (root / "notadate").exists()
    assert main.prune_uploads(30) == 0


def test_pruning_runs_with_the_normal_cleanup(client, tmp_path):
    (tmp_path / "uploads" / "20200101").mkdir(parents=True)
    (tmp_path / "uploads" / "20200101" / "f.txt").write_text("x")
    client.put("/api/settings", json={"retention_days": 30, "max_executions": 5000})
    main.prune_executions()
    assert not (tmp_path / "uploads" / "20200101").exists()
    client.put("/api/settings", json={"retention_days": 90, "max_executions": 5000})
