import json
import sqlite3

import pytest

from app import main
from conftest import FakeResponse, edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


@pytest.fixture()
def sqldir(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_SQLITE_DIR", str(tmp_path))
    return tmp_path


def run(client, nodes, edges):
    wid = make_flow(client, nodes, edges)
    res = client.post(f"/api/workflows/{wid}/run")
    assert res.status_code == 200
    return res.json()


def one(client, typ, payload=None, **data):
    nodes = [node("t", "trigger"), node("i", "input", json=json.dumps(payload if payload is not None else {"name": "Ada", "n": 3})), node("x", typ, **data), node("o", "output")]
    return run(client, nodes, [edge("t", "i"), edge("i", "x"), edge("x", "o")])


def cred(client, name, value):
    assert client.put(f"/api/credentials/{name}", json={"value": value}).status_code in (200, 201, 204)


# ---------------------------------------------------------------- SQL / SQLite
def test_sqlite_create_insert_select_with_parameters(client, sqldir):
    cred(client, "DB", "sqlite:///shop.db")
    assert one(client, "sql", credential="DB", query="CREATE TABLE people (name TEXT, n INTEGER)")["status"] == "success"
    res = one(client, "sql", credential="DB", query="INSERT INTO people VALUES (?, ?)", params='["{{name}}", {{n}}]')
    assert res["output"] == {"rows_affected": 1}
    res = one(client, "sql", credential="DB", query="SELECT name, n FROM people WHERE n >= ?", params="[1]")
    assert res["output"] == [{"name": "Ada", "n": 3}]


def test_sqlite_parameters_stop_sql_injection(client, sqldir):
    cred(client, "DB", "sqlite:///shop.db")
    one(client, "sql", credential="DB", query="CREATE TABLE people (name TEXT)")
    evil = "x'); DROP TABLE people; --"
    one(client, "sql", payload={"name": evil}, credential="DB", query="INSERT INTO people VALUES (?)", params='["{{name}}"]')
    rows = one(client, "sql", credential="DB", query="SELECT name FROM people")["output"]
    assert rows == [{"name": evil}]


@pytest.mark.parametrize("secret", ["sqlite:///../escape.db", "sqlite:////etc/passwd", "sqlite:///C:/windows/x.db", "sqlite://"])
def test_sqlite_cannot_leave_data_folder(client, sqldir, secret):
    cred(client, "DB", secret)
    assert one(client, "sql", credential="DB", query="SELECT 1")["status"] == "failed"


def test_sqlite_attach_is_denied_and_only_one_statement(client, sqldir):
    cred(client, "DB", "sqlite:///a.db")
    assert one(client, "sql", credential="DB", query="ATTACH DATABASE 'other.db' AS o")["status"] == "failed"
    assert one(client, "sql", credential="DB", query="SELECT 1; SELECT 2")["status"] == "failed"


def test_sql_row_limit_and_errors(client, sqldir, monkeypatch):
    cred(client, "DB", "sqlite:///a.db")
    monkeypatch.setattr(main, "SQL_ROW_LIMIT", 3)
    res = one(client, "sql", credential="DB", query="WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<10) SELECT x FROM c")
    assert res["status"] == "failed" and "LIMIT" in res["error"]
    bad = one(client, "sql", credential="DB", query="SELEC nope")
    assert bad["status"] == "failed" and "SQLite error" in bad["error"]
    assert one(client, "sql", credential="DB", query="")["status"] == "failed"
    assert one(client, "sql", credential="DB", query="SELECT 1", params="{not json")["status"] == "failed"


def test_sql_needs_a_known_scheme_and_credential(client):
    assert one(client, "sql", credential="NOPE", query="SELECT 1")["status"] == "failed"
    cred(client, "DB", "mysql://u:p@host/db")
    assert "sqlite:///" in one(client, "sql", credential="DB", query="SELECT 1")["error"]


def test_postgres_blocks_private_hosts_and_uses_parameters(client, monkeypatch):
    cred(client, "PG", "postgresql://user:pw%40x@db.example.com:5432/app?sslmode=require")
    seen = {}

    class Cur:
        description = [("a",)]
        def fetchmany(self, n): return [{"a": 1}]

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *e): return False
        def execute(self, q, params): seen.update(q=q, params=params); return Cur()

    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda **kw: (seen.update(kw=kw), Conn())[1])
    monkeypatch.setattr(main, "assert_public_host", lambda h, relaxed=False: seen.update(host=h, relaxed=relaxed))
    res = one(client, "sql", credential="PG", query="SELECT a FROM t WHERE n = %s", params="[{{n}}]")
    assert res["output"] == [{"a": 1}] and seen["params"] == [3] and seen["relaxed"] is True
    assert seen["kw"]["password"] == "pw@x" and seen["kw"]["host"] == "db.example.com" and seen["kw"]["sslmode"] == "require"
    monkeypatch.undo()
    cred(client, "PG2", "postgresql://u:p@localhost/db")
    assert one(client, "sql", credential="PG2", query="SELECT 1")["status"] == "failed"


# ---------------------------------------------------------------- RSS
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>One</title><link>https://e.com/1</link><pubDate>Mon, 01 Jan 2026</pubDate><description>&lt;b&gt;Hi&lt;/b&gt; there</description></item>
<item><title>Two</title><link>https://e.com/2</link></item></channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title><link href="https://e.com/a"/><updated>2026-01-01</updated><summary>S</summary></entry></feed>"""


def test_rss_and_atom(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, None, RSS)
    res = one(client, "rss", url="https://e.com/feed", limit="1")
    assert res["output"] == [{"title": "One", "link": "https://e.com/1", "published": "Mon, 01 Jan 2026", "summary": "Hi there"}]
    fake_http.reply["response"] = FakeResponse(200, None, ATOM)
    assert one(client, "rss", url="https://e.com/atom")["output"][0]["link"] == "https://e.com/a"


def test_rss_rejects_entities_and_garbage(client, fake_http):
    fake_http.reply["response"] = FakeResponse(200, None, '<!DOCTYPE x [<!ENTITY a "b">]><rss/>')
    assert "DOCTYPE" in one(client, "rss", url="https://e.com/f")["error"]
    fake_http.reply["response"] = FakeResponse(200, None, "not xml at all")
    assert one(client, "rss", url="https://e.com/f")["status"] == "failed"
    fake_http.reply["response"] = FakeResponse(200, {"a": 1})
    assert one(client, "rss", url="https://e.com/f")["status"] == "failed"


# ---------------------------------------------------------------- Telegram / GitHub
BOT = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def test_telegram_sends_and_hides_the_token(client, fake_http):
    cred(client, "BOT", BOT)
    res = one(client, "telegram", credential="BOT", chat_id="12345", text="Hi {{name}}")
    step = next(st for st in res["steps"] if st["node_id"] == "x")
    assert res["status"] == "success" and step["output"] == {"sent": True, "chat_id": "12345"} and res["output"]["name"] == "Ada"
    call = fake_http.calls[-1]
    assert call["url"].endswith("/sendMessage") and call["json"] == {"chat_id": "12345", "text": "Hi Ada"}
    assert BOT not in json.dumps(res)
    fake_http.reply["response"] = FakeResponse(401, None, "bad")
    failed = one(client, "telegram", credential="BOT", chat_id="12345", text="x")
    assert failed["status"] == "failed" and BOT not in json.dumps(failed)


@pytest.mark.parametrize("chat", ["", "abc", "1 2", "@a"])
def test_telegram_validates_chat_id(client, fake_http, chat):
    cred(client, "BOT", BOT)
    assert one(client, "telegram", credential="BOT", chat_id=chat, text="x")["status"] == "failed"


def test_telegram_rejects_a_wrong_token_shape(client, fake_http):
    cred(client, "BOT", "not-a-bot-token")
    assert one(client, "telegram", credential="BOT", chat_id="1", text="x")["status"] == "failed"


def test_github_create_and_list(client, fake_http):
    cred(client, "GH", "ghp_exampletoken12345")
    fake_http.reply["response"] = FakeResponse(201, {"number": 7, "html_url": "https://github.com/o/r/issues/7"})
    res = one(client, "github", credential="GH", repo="o/r", operation="create_issue", title="Bug {{name}}", body="b")
    assert res["output"] == {"number": 7, "url": "https://github.com/o/r/issues/7"}
    call = fake_http.calls[-1]
    assert call["json"]["title"] == "Bug Ada" and call["headers"]["Authorization"] == "Bearer ghp_exampletoken12345"
    assert "ghp_exampletoken12345" not in json.dumps(res)
    fake_http.reply["response"] = FakeResponse(200, [{"number": 1, "title": "T", "state": "open", "html_url": "u", "user": {"login": "z"}}, {"number": 2, "pull_request": {}}])
    res = one(client, "github", credential="GH", repo="o/r", operation="list_issues", state="all")
    assert res["output"] == [{"number": 1, "title": "T", "state": "open", "url": "u", "user": "z"}]


@pytest.mark.parametrize("repo", ["", "nope", "a/b/c", "../x/y", "a/b?x=1"])
def test_github_validates_repo(client, fake_http, repo):
    cred(client, "GH", "ghp_exampletoken12345")
    assert one(client, "github", credential="GH", repo=repo, operation="list_issues")["status"] == "failed"


# ---------------------------------------------------------------- Wait
def test_wait_is_capped_and_passes_data(client, monkeypatch):
    slept = []
    monkeypatch.setattr(main.time, "sleep", lambda s: slept.append(s))
    res = one(client, "wait", seconds="9999")
    assert res["status"] == "success" and res["output"] == {"name": "Ada", "n": 3}
    assert slept == [main.MAX_WAIT_SECONDS]


# ---------------------------------------------------------------- Respond to webhook
def hook_flow(client, respond_data, anon):
    nodes = [node("w", "webhook"), node("r", "respond", **respond_data), node("o", "output")]
    wid = make_flow(client, nodes, [edge("w", "r"), edge("r", "o")])
    assert client.post(f"/api/workflows/{wid}/active", json={"active": True}).status_code == 200
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    return anon.post(f"/api/hooks/{token}", json={"name": "Ada", "n": 5})


def test_webhook_returns_the_custom_response(client, anon):
    res = hook_flow(client, {"status": "201", "body": '{"hello": "{{body.name}}", "n": {{body.n}}}'}, anon)
    assert res.status_code == 201 and res.json() == {"hello": "Ada", "n": 5}


def test_webhook_text_response_and_quote_safety(client, anon):
    res = hook_flow(client, {"status": "200", "body": "plain {{body.name}}"}, anon)
    assert res.status_code == 200 and res.json() == "plain Ada"


def test_webhook_without_respond_node_still_returns_status(client, anon):
    nodes = [node("w", "webhook"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("w", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    body = anon.post(f"/api/hooks/{token}", json={}).json()
    assert body["status"] == "success" and "execution_id" in body


def test_respond_rejects_bad_status_and_never_leaks_credentials(client, anon):
    assert hook_flow(client, {"status": "99", "body": "x"}, anon).status_code == 500
    cred(client, "KEY", "topsecretvalue99")
    res = hook_flow(client, {"status": "200", "body": "{{cred.KEY}} topsecretvalue99"}, anon)
    assert "topsecretvalue99" not in res.text


def test_a_sub_workflow_cannot_answer_the_parent_webhook(client, anon):
    child = make_flow(client, [node("t", "trigger"), node("r", "respond", status="418", body="child")], [edge("t", "r")], name="child")
    nodes = [node("w", "webhook"), node("s", "subflow", workflow_id=child), node("o", "output")]
    wid = make_flow(client, nodes, [edge("w", "s"), edge("s", "o")])
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    token = client.get(f"/api/workflows/{wid}").json()["webhook_token"]
    res = anon.post(f"/api/hooks/{token}", json={})
    assert res.status_code == 200 and res.json()["status"] == "success"


def test_new_types_are_accepted(client):
    for t in ("sql", "rss", "telegram", "github", "wait", "respond"):
        make_flow(client, [node("t", "trigger"), node("x", t)], [edge("t", "x")])
