import csv
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from conftest import OWNER, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


# ---------------------------------------------------------------- run export
def parse(res):
    return list(csv.DictReader(io.StringIO(res.text)))


def test_export_lists_runs_as_csv(client):
    ok = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="Exports fine")
    bad = make_flow(client, [node("t", "trigger"), node("f", "fail", message="broke on purpose"), node("o", "output")], [edge("t", "f"), edge("f", "o")], name="Exports badly")
    client.post(f"/api/workflows/{ok}/run")
    client.post(f"/api/workflows/{bad}/run")
    res = client.get("/api/executions/export")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv") and "attachment" in res.headers["content-disposition"]
    rows = parse(res)
    assert list(rows[0].keys()) == ["execution_id", "workflow", "status", "trigger", "started_at", "finished_at", "duration_seconds", "steps", "error"]
    by_name = {r["workflow"]: r for r in rows}
    assert by_name["Exports fine"]["status"] == "success" and by_name["Exports fine"]["trigger"] == "manual" and by_name["Exports fine"]["steps"] == "2"
    assert by_name["Exports badly"]["status"] == "failed" and "broke on purpose" in by_name["Exports badly"]["error"]
    assert float(by_name["Exports fine"]["duration_seconds"]) >= 0


def test_export_filters_by_status_and_workflow(client):
    ok = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")], name="F-ok")
    bad = make_flow(client, [node("t", "trigger"), node("f", "fail", message="x"), node("o", "output")], [edge("t", "f"), edge("f", "o")], name="F-bad")
    client.post(f"/api/workflows/{ok}/run")
    client.post(f"/api/workflows/{bad}/run")
    assert {r["status"] for r in parse(client.get("/api/executions/export?status=failed"))} == {"failed"}
    assert {r["workflow"] for r in parse(client.get(f"/api/executions/export?workflow_id={ok}"))} == {"F-ok"}


def test_export_neutralises_spreadsheet_formulas(client):
    wid = make_flow(client, [node("t", "trigger"), node("f", "fail", message="=HYPERLINK(\"http://evil\")"), node("o", "output")], [edge("t", "f"), edge("f", "o")], name="=1+1")
    client.post(f"/api/workflows/{wid}/run")
    row = next(r for r in parse(client.get("/api/executions/export")) if r["workflow"].endswith("1+1"))
    assert row["workflow"].startswith("'=") and row["error"].startswith("'=")


def test_export_needs_a_session(client, anon):
    assert anon.get("/api/executions/export").status_code == 401


# ---------------------------------------------------------------- serving the built interface
def make_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>the app</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the folder", encoding="utf-8")
    return dist


def test_ui_is_served_with_a_single_page_fallback(tmp_path):
    target = FastAPI()

    @target.get("/api/ping")
    def ping(): return {"ok": True}

    assert main.mount_ui(target, make_dist(tmp_path)) is True
    c = TestClient(target)
    assert "the app" in c.get("/").text
    assert "the app" in c.get("/some/page/in/the/app").text  # the app handles its own routes
    assert c.get("/assets/app.js").text == "console.log('hi')"
    assert c.get("/favicon.svg").text == "<svg/>"
    assert c.get("/api/ping").json() == {"ok": True}  # real API routes still win
    assert c.get("/api/does-not-exist").status_code == 404
    assert c.get("/api").status_code == 404


def test_ui_cannot_be_used_to_read_files_outside_the_folder(tmp_path):
    target = FastAPI()
    main.mount_ui(target, make_dist(tmp_path))
    c = TestClient(target)
    for path in ("../secret.txt", "..%2fsecret.txt", "%2e%2e/secret.txt", "assets/../../secret.txt"):
        res = c.get("/" + path)
        assert "outside the folder" not in res.text, path


def test_no_ui_is_mounted_without_a_build(tmp_path):
    assert main.mount_ui(FastAPI(), tmp_path / "missing") is False


def test_the_real_app_does_not_serve_the_ui_unless_asked(anon):
    assert anon.get("/").status_code == 404
