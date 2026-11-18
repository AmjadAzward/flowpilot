import base64
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import main
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db:
        db.execute("DELETE FROM waits")


def run(client, nodes, edges):
    wid = make_flow(client, nodes, edges)
    return client.post(f"/api/workflows/{wid}/run").json()


# ---------------------------------------------------------------- repeat until
def repeat_flow(client, rep_data, start=None):
    nodes = [node("t", "trigger"), node("i", "input", json=json.dumps(start if start is not None else {"n": 0})), node("r", "repeat", **rep_data),
             node("c", "code", code="result = {'n': input['n'] + 1}"), node("o", "output")]
    return run(client, nodes, [edge("t", "i"), edge("i", "r"), edge("r", "c", "each"), edge("r", "o", "done")])


def test_repeat_runs_the_body_until_the_condition_is_met(client):
    res = repeat_flow(client, {"path": "n", "op": "gt", "equals": "2", "max": "10"})
    assert res["status"] == "success" and res["output"] == {"n": 3}
    step = next(s for s in res["steps"] if s["node_id"] == "r")
    assert step["output"] == {"attempts": 3, "condition_met": True}
    assert [s["iteration"] for s in res["steps"] if s["node_id"] == "c"] == [[0], [1], [2]]


def test_repeat_stops_at_the_limit_and_can_fail(client):
    res = repeat_flow(client, {"path": "n", "op": "gt", "equals": "99", "max": "3"})
    assert res["status"] == "success" and res["output"] == {"n": 3}
    assert next(s for s in res["steps"] if s["node_id"] == "r")["output"] == {"attempts": 3, "condition_met": False}
    res = repeat_flow(client, {"path": "n", "op": "gt", "equals": "99", "max": "2", "fail_if_not_met": "true"})
    assert res["status"] == "failed" and "not met after 2" in res["error"]


def test_repeat_limit_is_capped_and_needs_a_body(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_REPEATS", 2)
    res = repeat_flow(client, {"path": "n", "op": "gt", "equals": "99", "max": "500"})
    assert next(s for s in res["steps"] if s["node_id"] == "r")["output"]["attempts"] == 2
    nodes = [node("t", "trigger"), node("r", "repeat"), node("o", "output")]
    assert run(client, nodes, [edge("t", "r"), edge("r", "o", "done")])["status"] == "failed"
    assert repeat_flow(client, {"max": "abc"})["status"] == "failed"


# ---------------------------------------------------------------- parallel branches
def test_ready_branches_run_at_the_same_time(client):
    nodes = [node("t", "trigger"), node("a", "delay", seconds="1"), node("b", "delay", seconds="1"), node("c", "delay", seconds="1"),
             node("m", "merge", mode="append"), node("o", "output")]
    edges = [edge("t", "a"), edge("t", "b"), edge("t", "c"), edge("a", "m"), edge("b", "m"), edge("c", "m"), edge("m", "o")]
    t0 = time.time()
    res = run(client, nodes, edges)
    took = time.time() - t0
    assert res["status"] == "success" and len(res["output"]) == 3
    assert took < 2.6, f"three one-second branches took {took:.1f}s, so they did not run in parallel"


def test_parallel_nodes_really_use_different_threads(client, monkeypatch):
    seen, barrier = set(), threading.Barrier(2, timeout=5)
    real = main.step_action

    def spy(n, previous):
        if n["id"] in ("a", "b"):
            seen.add(threading.get_ident())
            barrier.wait()  # only passes if both are running at once
        return real(n, previous)

    monkeypatch.setattr(main, "step_action", spy)
    nodes = [node("t", "trigger"), node("a", "delay", seconds="0"), node("b", "delay", seconds="0"), node("m", "merge"), node("o", "output")]
    res = run(client, nodes, [edge("t", "a"), edge("t", "b"), edge("a", "m"), edge("b", "m"), edge("m", "o")])
    assert res["status"] == "success" and len(seen) == 2


def test_parallel_results_and_steps_are_recorded_for_every_branch(client, fake_http):
    client.put("/api/credentials/KEY", json={"value": "topsecretvalue99"})
    nodes = [node("t", "trigger"), node("a", "http", url="https://api.example.com/a", auth_type="bearer", auth_credential="KEY"),
             node("b", "http", url="https://api.example.com/b?k={{cred.KEY}}"), node("m", "merge"), node("o", "output")]
    res = run(client, nodes, [edge("t", "a"), edge("t", "b"), edge("a", "m"), edge("b", "m"), edge("m", "o")])
    assert res["status"] == "success" and len(fake_http.calls) == 2  # credentials work inside worker threads
    assert {s["node_id"] for s in res["steps"]} == {"t", "a", "b", "m", "o"} and all(s["status"] == "success" for s in res["steps"])
    assert "topsecretvalue99" not in json.dumps(res)


def test_a_failing_parallel_branch_fails_the_run_but_keeps_the_others_steps(client):
    nodes = [node("t", "trigger"), node("a", "code", code="result = 1/0"), node("b", "code", code="result = 2"), node("m", "merge"), node("o", "output")]
    res = run(client, nodes, [edge("t", "a"), edge("t", "b"), edge("a", "m"), edge("b", "m"), edge("m", "o")])
    assert res["status"] == "failed" and "ZeroDivision" in res["error"] or "division" in res["error"]
    steps = {s["node_id"]: s["status"] for s in res["steps"]}
    assert steps["a"] == "error" and steps["b"] == "success" and "m" not in steps


def test_parallelism_is_bounded(client, monkeypatch):
    active, peak, lock = [0], [0], threading.Lock()
    real = main.step_action

    def spy(n, previous):
        if n["id"].startswith("p"):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.2)
            with lock: active[0] -= 1
        return real(n, previous)

    monkeypatch.setattr(main, "step_action", spy)
    branches = [node(f"p{i}", "delay", seconds="0") for i in range(7)]
    nodes = [node("t", "trigger"), *branches, node("m", "merge"), node("o", "output")]
    edges = [edge("t", b["id"]) for b in branches] + [edge(b["id"], "m") for b in branches] + [edge("m", "o")]
    assert run(client, nodes, edges)["status"] == "success"
    assert 2 <= peak[0] <= main.MAX_PARALLEL


# ---------------------------------------------------------------- files
@pytest.fixture()
def files(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_FILES_DIR", str(tmp_path))
    return tmp_path


def one(client, typ, payload=None, **data):
    nodes = [node("t", "trigger"), node("i", "input", json=json.dumps(payload if payload is not None else {"name": "Ada"})), node("x", typ, **data), node("o", "output")]
    res = run(client, nodes, [edge("t", "i"), edge("i", "x"), edge("x", "o")])
    res["step"] = next((s for s in res["steps"] if s["node_id"] == "x"), {})
    return res


def test_file_write_read_list_append_delete(client, files):
    res = one(client, "file", operation="write", path="reports/{{name}}.txt", content="Hello {{name}}")
    assert res["step"]["output"] == {"path": "reports/Ada.txt", "size": 9} and (files / "reports" / "Ada.txt").read_text() == "Hello Ada"
    one(client, "file", operation="write", path="reports/Ada.txt", content="!", mode="append")
    read = one(client, "file", operation="read", path="reports/Ada.txt")["step"]["output"]
    assert read["text"] == "Hello Ada!" and read["size"] == 10 and read["mime"] == "text/plain"
    assert one(client, "file", operation="list")["step"]["output"] == [{"path": "reports/Ada.txt", "size": 10}]
    assert one(client, "file", operation="delete", path="reports/Ada.txt")["step"]["output"]["deleted"] is True
    assert one(client, "file", operation="read", path="reports/Ada.txt")["status"] == "failed"


def test_file_base64_roundtrip(client, files):
    data = bytes(range(256))
    one(client, "file", operation="write", path="bin.dat", content=base64.b64encode(data).decode(), encoding="base64")
    assert (files / "bin.dat").read_bytes() == data
    out = one(client, "file", operation="read", path="bin.dat", **{"as": "base64"})["step"]["output"]
    assert base64.b64decode(out["base64"]) == data and out["mime"] == "application/octet-stream"
    assert one(client, "file", operation="write", path="x.dat", content="not base64!!", encoding="base64")["status"] == "failed"


@pytest.mark.parametrize("path", ["../escape.txt", "a/../../b.txt", "/etc/passwd", "C:/x.txt", "a//b", "dir/", "", ".hidden/../x", "bad*name"])
def test_file_paths_cannot_leave_the_folder(client, files, path):
    assert one(client, "file", operation="write", path=path, content="x")["status"] == "failed"
    assert not list(files.parent.glob("escape.txt"))


def test_file_size_limits(client, files, monkeypatch):
    monkeypatch.setattr(main, "FILE_MAX", 10)
    assert one(client, "file", operation="write", path="big.txt", content="x" * 11)["status"] == "failed"
    (files / "ok.txt").write_text("y" * 20)
    assert one(client, "file", operation="read", path="ok.txt")["status"] == "failed"
    one(client, "file", operation="write", path="a.txt", content="123456")
    assert one(client, "file", operation="write", path="a.txt", content="123456", mode="append")["status"] == "failed"
    assert one(client, "file", operation="frobnicate")["status"] == "failed"


def test_http_response_can_be_saved_as_a_file(client, files, fake_http):
    class Resp(FakeResponse):
        headers = {"content-type": "image/png"}
    fake_http.reply["response"] = Resp(200, None, "PNGDATA")
    res = one(client, "http", url="https://files.example.com/pic.png", method="GET", save_as="downloads/{{name}}.png")
    assert res["step"]["output"] == {"path": "downloads/Ada.png", "size": 7, "content_type": "image/png"}
    assert (files / "downloads" / "Ada.png").read_bytes() == b"PNGDATA"
    assert one(client, "http", url="https://files.example.com/pic.png", method="GET", save_as="../x.png")["status"] == "failed"


# ---------------------------------------------------------------- email-in trigger
RAW_MAIL = (b"From: Bob <bob@example.com>\r\nTo: me@example.com\r\nSubject: Invoice 42\r\nDate: Mon, 01 Jan 2026 09:00:00 +0000\r\nMessage-ID: <1@x>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\nPlease pay.\r\n")
RAW_OTHER = RAW_MAIL.replace(b"Invoice 42", b"Newsletter").replace(b"bob@example.com", b"news@example.org")


class FakeIMAP:
    boxes: dict = {}
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.seen, self.logged_in = host, port, [], None
        FakeIMAP.instances.append(self)

    def login(self, user, pw):
        if pw == "badpass": raise main.imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        self.logged_in = (user, pw)

    def select(self, box): self.box = box
    def search(self, *_): return "OK", [b" ".join(FakeIMAP.boxes.get("unseen", []))]
    def fetch(self, mid, _): return "OK", [(b"1 (BODY[] {10}", FakeIMAP.boxes["messages"][mid]), b")"]
    def store(self, mid, *_): self.seen.append(mid)
    def logout(self): pass


@pytest.fixture()
def imap(monkeypatch):
    FakeIMAP.boxes = {"unseen": [b"1", b"2"], "messages": {b"1": RAW_MAIL, b"2": RAW_OTHER}}
    FakeIMAP.instances = []
    monkeypatch.setattr(main.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(main, "assert_public_host", lambda h, relaxed=False: None)
    return FakeIMAP


def inbox_flow(client, **data):
    client.put("/api/credentials/MAIL", json={"value": "imaps://me%40example.com:secret-pass@imap.example.com:993"})
    nodes = [node("e", "email_in", credential="MAIL", minutes="5", **data), node("m", "template", template="{{subject}} from {{from}}"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("e", "m"), edge("m", "o")])
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 200
    with main.conn() as db:
        db.execute("UPDATE workflows SET next_run_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), wid))
    return wid


def test_new_mail_starts_one_run_per_message_and_is_marked_read(client, imap):
    wid = inbox_flow(client)
    assert main.scheduler_tick() == 1
    runs = client.get("/api/executions", params={"workflow_id": wid}).json()
    assert len(runs) == 2 and all(r["trigger"] == "email_in" and r["status"] == "success" for r in runs)
    assert sorted(r["output"] for r in runs) == ["Invoice 42 from Bob <bob@example.com>", "Newsletter from Bob <news@example.org>"]
    assert imap.instances[0].seen == [b"1", b"2"] and imap.instances[0].logged_in == ("me@example.com", "secret-pass")
    nxt = client.get(f"/api/workflows/{wid}").json()["next_run_at"]
    assert datetime.fromisoformat(nxt) > datetime.now(timezone.utc) + timedelta(minutes=4)


def test_filters_leave_other_messages_unread(client, imap):
    wid = inbox_flow(client, subject_filter="invoice")
    main.scheduler_tick()
    runs = client.get("/api/executions", params={"workflow_id": wid}).json()
    assert len(runs) == 1 and "Invoice 42" in runs[0]["output"] and imap.instances[0].seen == [b"1"]
    wid2 = inbox_flow(client, from_filter="nobody@nowhere")
    main.scheduler_tick()
    assert client.get("/api/executions", params={"workflow_id": wid2}).json() == []


def test_message_fields_and_attachments(client, imap):
    raw = (b"From: a@example.com\r\nTo: b@example.com\r\nSubject: With file\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=XX\r\n\r\n"
           b"--XX\r\nContent-Type: text/plain\r\n\r\nBody text\r\n--XX\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"bill.pdf\"\r\n\r\nPDF\r\n--XX--\r\n")
    m = main.parse_message(raw)
    assert m["subject"] == "With file" and m["text"] == "Body text" and m["attachments"] == ["bill.pdf"]
    html = main.parse_message(b"Subject: h\r\nContent-Type: text/html\r\n\r\n<p>Hi <b>there</b></p>\r\n")
    assert "<" not in html["text"] and "Hi" in html["text"]


def test_inbox_errors_are_recorded_as_failed_runs_without_secrets(client, imap):
    client.put("/api/credentials/MAIL", json={"value": "imaps://me%40example.com:badpass@imap.example.com:993"})
    nodes = [node("e", "email_in", credential="MAIL", minutes="5"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("e", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    with main.conn() as db:
        db.execute("UPDATE workflows SET next_run_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), wid))
    main.scheduler_tick()
    runs = client.get("/api/executions", params={"workflow_id": wid}).json()
    assert len(runs) == 1 and runs[0]["status"] == "failed" and "Could not read the inbox" in runs[0]["error"] and "badpass" not in json.dumps(runs)


def test_activation_validates_the_email_trigger(client):
    wid = make_flow(client, [node("e", "email_in", credential="", minutes="5"), node("o", "output")], [edge("e", "o")])
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 400
    wid = make_flow(client, [node("e", "email_in", credential="MAIL", minutes="0"), node("o", "output")], [edge("e", "o")])
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 400


def test_manual_run_uses_a_sample_email_and_listing_shows_the_trigger(client):
    wid = make_flow(client, [node("e", "email_in", credential="MAIL"), node("m", "template", template="{{subject}}"), node("o", "output")], [edge("e", "m"), edge("m", "o")])
    assert client.post(f"/api/workflows/{wid}/run").json()["output"] == "Sample message"
    assert next(w for w in client.get("/api/workflows").json() if w["id"] == wid)["trigger"] == "email_in"


def test_imap_credential_shape_and_private_hosts(client, monkeypatch):
    client.put("/api/credentials/MAIL", json={"value": "http://example.com"})
    with pytest.raises(ValueError):
        tok = main._secrets_var.set(main.load_secrets())
        try: main.fetch_unseen({"credential": "MAIL"})
        finally: main._secrets_var.reset(tok)
    client.put("/api/credentials/MAIL", json={"value": "imaps://u:p@localhost:993"})
    monkeypatch.delenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", raising=False)
    with pytest.raises(ValueError):
        tok = main._secrets_var.set(main.load_secrets())
        try: main.fetch_unseen({"credential": "MAIL"})
        finally: main._secrets_var.reset(tok)


def test_new_types_are_accepted(client):
    for t in ("repeat", "file"):
        make_flow(client, [node("t", "trigger"), node("x", t)], [edge("t", "x")])
