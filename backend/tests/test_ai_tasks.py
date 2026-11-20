import json

import pytest

from conftest import FakeResponse, edge, make_flow, node

AI = "http://127.0.0.1:11434/v1?model=llama3.2"
EMAIL = "Hi team, I'm Dana Smith (dana@acme.io). Order 1,250 units by 2025-03-01. Please confirm. Thanks!"


@pytest.fixture(autouse=True)
def _env(clean_state, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS", "1")
    yield


def reply(text):
    return FakeResponse(200, {"choices": [{"message": {"content": text}}]})


def ai(client, fake_http, answer, **data):
    client.put("/api/credentials/LLM", json={"value": AI})
    fake_http.reply["response"] = reply(answer) if isinstance(answer, str) else answer
    nodes = [node("t", "trigger"), node("i", "input", json=json.dumps({"mail": EMAIL})), node("a", "ai", credential="LLM", **data), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "a"), edge("a", "o")])
    return client.post(f"/api/workflows/{wid}/run").json()


def system_prompt(fake_http):
    return fake_http.calls[-1]["json"]["messages"][0]["content"]


def test_extract_returns_only_the_requested_fields_with_types(client, fake_http):
    schema = {"name": "the sender's full name", "email": {"type": "text", "description": "email address"}, "quantity": {"type": "integer", "description": "units"}, "deadline": "due date", "urgent": {"type": "true_false"}, "tags": {"type": "list"}}
    answer = '```json\n{"name": "Dana Smith", "email": "dana@acme.io", "quantity": "1,250", "deadline": "2025-03-01", "urgent": "no", "tags": "order", "extra": "ignored"}\n```'
    res = ai(client, fake_http, answer, task="extract", prompt="{{mail}}", schema=json.dumps(schema))
    assert res["status"] == "success"
    assert res["output"] == {"name": "Dana Smith", "email": "dana@acme.io", "quantity": 1250, "deadline": "2025-03-01", "urgent": False, "tags": ["order"]}
    sys = system_prompt(fake_http)
    assert "quantity (integer): units" in sys and "name (text): the sender's full name" in sys and "null" in sys
    assert fake_http.calls[-1]["json"]["messages"][1]["content"] == EMAIL


def test_extract_fills_missing_and_unparsable_values_with_null(client, fake_http):
    res = ai(client, fake_http, '{"quantity": "lots", "email": null}', task="extract", prompt="{{mail}}", schema=json.dumps({"quantity": {"type": "number"}, "email": "e", "phone": "p"}))
    assert res["output"] == {"quantity": None, "email": None, "phone": None}


@pytest.mark.parametrize("schema", ["", "not json", "[]", "{}", '{"bad name": "x"}', '{"a": {"type": "color"}}', json.dumps({f"f{i}": "x" for i in range(31)})])
def test_extract_rejects_a_bad_schema_before_calling_the_model(client, fake_http, schema):
    res = ai(client, fake_http, "{}", task="extract", prompt="{{mail}}", schema=schema)
    assert res["status"] == "failed" and fake_http.calls == []


def test_extract_needs_a_json_object_back(client, fake_http):
    assert ai(client, fake_http, "I cannot do that", task="extract", prompt="x", schema='{"a": "b"}')["status"] == "failed"
    assert ai(client, fake_http, "[1, 2]", task="extract", prompt="x", schema='{"a": "b"}')["status"] == "failed"


def test_classify_picks_a_listed_category_case_insensitively(client, fake_http):
    res = ai(client, fake_http, '{"category": "BILLING", "confidence": 0.93}', task="classify", prompt="{{mail}}", categories="Billing, Support, Sales")
    assert res["status"] == "success" and res["output"] == {"category": "Billing", "confidence": 0.93, "model": "llama3.2"}
    assert "Billing, Support, Sales" in system_prompt(fake_http)


def test_classify_refuses_an_answer_outside_the_list_and_bad_category_lists(client, fake_http):
    res = ai(client, fake_http, '{"category": "Spam"}', task="classify", prompt="x", categories="Billing, Support")
    assert res["status"] == "failed" and "not one of the categories" in res["error"]
    for cats in ("", "Only", "A, a", ", ,", ",".join(f"c{i}" for i in range(21))):
        assert ai(client, fake_http, '{"category": "c1"}', task="classify", prompt="x", categories=cats)["status"] == "failed", cats


def test_classify_accepts_a_bare_word_and_clamps_confidence(client, fake_http):
    assert ai(client, fake_http, '"Support"', task="classify", prompt="x", categories="Billing, Support")["output"]["category"] == "Support"
    assert ai(client, fake_http, '{"category": "Support", "confidence": 7}', task="classify", prompt="x", categories="Billing, Support")["output"]["confidence"] == 1.0
    assert ai(client, fake_http, '{"category": "Support", "confidence": "high"}', task="classify", prompt="x", categories="Billing, Support")["output"]["confidence"] is None


def test_sentiment_uses_fixed_categories(client, fake_http):
    res = ai(client, fake_http, '{"category": "negative", "confidence": 0.8}', task="sentiment", prompt="{{mail}}")
    assert res["output"]["category"] == "negative" and "positive, neutral, negative" in system_prompt(fake_http)


def test_summarize_lengths(client, fake_http):
    res = ai(client, fake_http, "  Dana wants 1,250 units by March.  ", task="summarize", prompt="{{mail}}", length="medium")
    assert res["output"] == {"text": "Dana wants 1,250 units by March.", "model": "llama3.2"} and "short paragraph" in system_prompt(fake_http)
    assert ai(client, fake_http, "x", task="summarize", prompt="{{mail}}", length="huge")["status"] == "failed"


def test_unknown_task_and_empty_text_fail_without_calling_the_model(client, fake_http):
    assert ai(client, fake_http, "x", task="poetry", prompt="x")["status"] == "failed"
    res = ai(client, fake_http, "x", task="summarize", prompt="   ")
    assert res["status"] == "failed" and "text" in res["error"] and fake_http.calls == []


def test_the_default_task_is_unchanged(client, fake_http):
    res = ai(client, fake_http, "Hello", prompt="Greet", system="Be brief")
    assert res["output"] == {"text": "Hello", "model": "llama3.2"} and fake_http.calls[-1]["json"]["messages"][0]["content"] == "Be brief"


def test_the_credential_never_appears_in_results(client, fake_http):
    client.put("/api/credentials/LLM", json={"value": "http://127.0.0.1:11434/v1?model=llama3.2&key=sk-secret-key-123"})
    fake_http.reply["response"] = FakeResponse(500, {"error": "boom"})
    nodes = [node("t", "trigger"), node("a", "ai", credential="LLM", task="summarize", prompt="text")]
    wid = make_flow(client, nodes, [edge("t", "a")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "sk-secret-key-123" not in json.dumps(res)


def test_translate_and_rewrite(client, fake_http):
    res = ai(client, fake_http, " Bonjour Dana ", task="translate", prompt="Hello Dana", language="French")
    assert res["output"] == {"text": "Bonjour Dana", "model": "llama3.2"} and "into French" in system_prompt(fake_http)
    for bad in ("", "French; drop", "x"):
        assert ai(client, fake_http, "x", task="translate", prompt="Hi", language=bad)["status"] == "failed"
    res = ai(client, fake_http, "Dear Dana, thank you.", task="rewrite", prompt="thx dana", style="formal")
    assert res["output"]["text"] == "Dear Dana, thank you." and "formal tone" in system_prompt(fake_http)
    assert ai(client, fake_http, "x", task="rewrite", prompt="hi", style="pirate")["status"] == "failed"


def test_invalid_json_gets_one_corrective_retry(client, monkeypatch):
    from app import main
    answers = iter(["Sure! Here you go: name is Dana", '{"name": "Dana"}'])
    seen = []

    def fake_request(method, url, headers, **kwargs):
        seen.append(kwargs["json"]["messages"])
        return {"choices": [{"message": {"content": next(answers)}}]}
    monkeypatch.setattr(main, "do_request", fake_request)
    client.put("/api/credentials/LLM", json={"value": AI})
    nodes = [node("t", "trigger"), node("a", "ai", credential="LLM", task="extract", prompt="Dana wrote this", schema='{"name": "who"}')]
    wid = make_flow(client, nodes, [edge("t", "a")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success" and res["output"] == {"name": "Dana"}
    assert len(seen) == 2 and seen[1][-1]["content"].startswith("That was not valid JSON") and seen[1][-2]["role"] == "assistant"


def test_a_second_invalid_reply_fails_after_exactly_one_retry(client, monkeypatch):
    from app import main
    count = []

    def fake_request(method, url, headers, **kwargs):
        count.append(1)
        return {"choices": [{"message": {"content": "still not json"}}]}
    monkeypatch.setattr(main, "do_request", fake_request)
    client.put("/api/credentials/LLM", json={"value": AI})
    wid = make_flow(client, [node("t", "trigger"), node("a", "ai", credential="LLM", task="classify", prompt="x", categories="A, B")], [edge("t", "a")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "not valid JSON" in res["error"] and len(count) == 2
