import pytest
from fastapi.testclient import TestClient

from app import main
from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield
    with main.conn() as db: db.execute("DELETE FROM settings WHERE key='default_error_workflow'")


def handler(client, name="Shared handler"):
    return make_flow(client, [node("t", "trigger"), node("m", "template", template="ALERT {{workflow.name}}: {{error}}"), node("o", "output")], [edge("t", "m"), edge("m", "o")], name=name)


def failing(client, name="Breaks", **cfg):
    return make_flow(client, [node("t", "trigger", **cfg), node("f", "fail", message="boom")], [edge("t", "f")], name=name)


def handled(client, wid):
    return client.get("/api/executions", params={"workflow_id": wid}).json()


def test_it_starts_unset_and_can_be_set_and_cleared(client):
    assert client.get("/api/settings/error-workflow").json() == {"workflow_id": ""}
    h = handler(client)
    assert client.put("/api/settings/error-workflow", json={"workflow_id": h}).json() == {"workflow_id": h}
    assert client.get("/api/settings/error-workflow").json() == {"workflow_id": h}
    assert client.put("/api/settings/error-workflow", json={"workflow_id": ""}).json() == {"workflow_id": ""}
    assert client.get("/api/settings/error-workflow").json() == {"workflow_id": ""}


def test_an_unknown_workflow_is_refused(client):
    assert client.put("/api/settings/error-workflow", json={"workflow_id": "nope"}).status_code == 404


def test_any_failing_workflow_without_its_own_handler_uses_the_shared_one(client):
    h = handler(client)
    client.put("/api/settings/error-workflow", json={"workflow_id": h})
    bad = failing(client)
    assert client.post(f"/api/workflows/{bad}/run").json()["status"] == "failed"
    runs = handled(client, h)
    assert len(runs) == 1 and runs[0]["trigger"] == "error" and runs[0]["output"] == "ALERT Breaks: boom"


def test_a_workflows_own_handler_wins(client):
    shared, own = handler(client, "Shared"), handler(client, "Own")
    client.put("/api/settings/error-workflow", json={"workflow_id": shared})
    bad = failing(client, error_workflow=own)
    client.post(f"/api/workflows/{bad}/run")
    assert len(handled(client, own)) == 1 and handled(client, shared) == []


def test_it_does_not_run_on_success_for_itself_or_in_a_loop(client):
    h = make_flow(client, [node("t", "trigger"), node("f", "fail", message="handler broke")], [edge("t", "f")], name="Broken handler")
    client.put("/api/settings/error-workflow", json={"workflow_id": h})
    ok = make_flow(client, [node("t", "trigger"), node("o", "output")], [edge("t", "o")])
    client.post(f"/api/workflows/{ok}/run")
    assert handled(client, h) == []
    bad = failing(client)
    client.post(f"/api/workflows/{bad}/run")
    assert len(handled(client, h)) == 1  # the failing handler ran once and did not trigger itself again
    client.post(f"/api/workflows/{h}/run")  # the handler failing by itself does not call itself
    assert len(handled(client, h)) == 2


def test_only_owners_and_admins_can_change_it(client):
    h = handler(client)
    client.post("/api/users", json={"username": "err-editor", "password": "long-enough-pw", "role": "editor"})
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "err-editor", "password": "long-enough-pw"}).status_code == 200
    assert c.put("/api/settings/error-workflow", json={"workflow_id": h}).status_code == 403
    users = client.get("/api/users").json()
    client.delete(f"/api/users/{next(u['id'] for u in users if u['username'] == 'err-editor')}")
