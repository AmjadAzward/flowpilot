import json
import time

import pytest

from app import main
from conftest import edge, make_flow, node

DATA = {"price": 10, "qty": 3, "name": "ada lovelace", "user": {"first": "Ada", "tags": ["a", "b"]}, "items": [{"n": 1}, {"n": 2}, {"n": 3}], "total": 250,
        "flag": True, "none": None, "text": "  pad  ", "csv": "x,y,z"}


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def ev(expr, data=None):
    return main.eval_expression(expr, DATA if data is None else data)


# ---------------------------------------------------------------- what works
@pytest.mark.parametrize("expr,expected", [
    ("price * qty", 30), ("price + qty * 2", 16), ("(price + qty) * 2", 26), ("price / 4", 2.5), ("price // 3", 3), ("price % 3", 1), ("2 ** 10", 1024), ("-price", -10),
    ("round(price / 3, 2)", 3.33), ("abs(-5)", 5), ("min(price, qty)", 3), ("max(1, 2, 3)", 3), ("sum([1, 2, 3])", 6),
    ("name.upper()", "ADA LOVELACE"), ("name.title()", "Ada Lovelace"), ("text.strip()", "pad"), ("name.replace('a', 'A')", "AdA lovelAce"),
    ("name.split(' ')[0]", "ada"), ("csv.split(',')", ["x", "y", "z"]), ("'-'.join(csv.split(','))", "x-y-z"), ("name.startswith('ada')", True), ("name.endswith('x')", False),
    ("len(items)", 3), ("len(name)", 12), ("items[0].n", 1), ("items[-1].n", 3), ("items[9]", None), ("items[1:]", [{"n": 2}, {"n": 3}]), ("name[:3]", "ada"),
    ("user.first + '!'", "Ada!"), ("user['first']", "Ada"), ("user.tags[1]", "b"), ("user.missing", None), ("user.get('first')", "Ada"), ("user.keys()", ["first", "tags"]),
    ("total > 100", True), ("total > 100 and 'big' or 'small'", "big"), ("'big' if total > 100 else 'small'", "big"), ("price == 10", True), ("1 < price < 20", True), ("price != 10", False),
    ("'a' in name", True), ("'z' not in name", True), ("not flag", False), ("none is None", True), ("flag and price", 10), ("none or 'fallback'", "fallback"),
    ("str(price) + 'x'", "10x"), ("int('42') + 1", 43), ("float('1.5') * 2", 3.0), ("sorted([3, 1, 2])", [1, 2, 3]), ("[1, 2] + [3]", [1, 2, 3]), ("{'a': price}['a']", 10),
    ("zero_or_missing", None), ("data.price", 10), ("'ab' * 3", "ababab"), ("zfill_test", None), ("str(7).zfill(3)", "007"),
])
def test_expressions_evaluate(expr, expected):
    assert ev(expr) == expected


def test_today_and_now_return_iso_text():
    assert len(ev("today()")) == 10 and "T" in ev("now()")


def test_missing_names_are_empty_not_errors():
    assert ev("nothing") is None and ev("nothing.deeper") is None and ev("(nothing or 'x')") == "x"
    assert ev("price", [1, 2]) is None and ev("data[1]", [1, 2]) == 2 and ev("len(data)", [1, 2]) == 2


# ---------------------------------------------------------------- what is refused
@pytest.mark.parametrize("expr", [
    "__import__('os')", "__builtins__", "name.__class__", "data.__class__", "().__class__.__bases__", "''.join.__self__", "[].__len__()", "name.__len__()",
    "(lambda: 1)()", "[x for x in items]", "{x: 1 for x in items}", "(x := 1)", "f'{name}'", "open('x')", "eval('1')", "exec('x=1')", "compile('1','a','eval')",
    "getattr(name, 'upper')", "setattr(data, 'a', 1)", "type(name)", "print(1)", "input()", "globals()", "locals()", "vars()", "dir()", "id(name)", "hash(name)",
    "name.format('x')", "name.format_map({})", "name.encode()", "name.translate({})", "name.splitlines()", "items.append(1)", "items.pop()", "user.update({})", "user.pop('first')",
    "user.setdefault('a', 1)", "name.get('x')", "items.keys()", "csv.keys()", "name.join(1)", "cred.KEY", "cred", "_private", "user._x", "await x", "yield", "name; 1",
    "lambda x: x", "*items", "max(*items)", "max(key=len)", "sorted(items, key=len)", "name.upper(1, 2, 3)", "1 if", "((", "name..", "@", "$x", "x = 1", "import os",
    "price @ qty", "price << 2", "price & 1", "price | 1", "price ^ 1", "~price", "'a' 'b'.__class__", "b'bytes'", "1j", "...",
])
def test_unsafe_or_unsupported_expressions_are_rejected(expr):
    with pytest.raises(main.ExprError):
        ev(expr)


@pytest.mark.parametrize("expr", ["price / 0", "price // 0", "price % 0", "'a' + 1", "1 + 'a'", "price + none", "flag + 1", "[1] < 'a'", "int('abc')", "float('x')", "round('x')", "min()", "len(5)"])
def test_type_and_math_errors_are_clean_errors(expr):
    with pytest.raises(main.ExprError):
        ev(expr)


def test_size_and_complexity_limits():
    for expr in ["'x' * 1000000", "'x' * 100000 * 100000", "[1] * 10 ** 6", "10 ** 100", "9 ** 9 ** 9", "(10 ** 7) ** 5", "2 ** 65", "str(10) * 99999999"]:
        with pytest.raises(main.ExprError):
            ev(expr)
    with pytest.raises(main.ExprError): ev(" + ".join(["1"] * 400))  # too many nodes
    with pytest.raises(main.ExprError): ev("(" * 300 + "1" + ")" * 300)
    with pytest.raises(main.ExprError): ev("1 + " * 150 + "1")
    with pytest.raises(main.ExprError): ev("x" * 1001)
    with pytest.raises(main.ExprError): ev("\x00")
    with pytest.raises(main.ExprError): ev("[" + ",".join(["1"] * 1100) + "]")


def test_a_hostile_expression_cannot_hang_or_exhaust_memory():
    t0 = time.time()
    for expr in ["'a' * 199999 * 199999", "2 ** 64 ** 64", "'x'.zfill(10 ** 9)", "[0] * (10 ** 9)"]:
        try: ev(expr)
        except main.ExprError: pass
    assert time.time() - t0 < 2


def test_zfill_cannot_allocate_huge_strings():
    with pytest.raises(main.ExprError):
        ev("'x'.zfill(1000000000)")


# ---------------------------------------------------------------- inside templates
def test_templates_mix_text_paths_and_expressions():
    out = main.render_template("Total: {{ price * qty }} for {{ user.first }} ({{ name.upper() }}) {{ 'big' if total > 100 else 'small' }}", DATA)
    assert out == "Total: 30 for Ada (ADA LOVELACE) big"


def test_plain_paths_keep_working_exactly_as_before():
    assert main.render_template("{{name}}|{{ name }}|{{user.first}}|{{items.1.n}}|{{missing}}|{{}}", {"name": "n", "user": {"first": "u"}, "items": [{"n": 1}, {"n": 2}]}) == 'n|n|u|2||{"name": "n", "user": {"first": "u"}, "items": [{"n": 1}, {"n": 2}]}'
    assert main.render_template("{{a-b}}", {"a-b": "hyphen key"}) == "hyphen key"  # no spaces: still a plain path
    assert main.render_template("{{ a - b }}", {"a": 5, "b": 2}) == "3"  # spaces: an expression


def test_results_are_rendered_as_json_or_text():
    assert main.render_template("{{ [1, 2] }}", {}) == "[1, 2]" and main.render_template("{{ flag }}", {"flag": True}) == "true"
    assert main.render_template("{{ none }}", {"none": None}) == "" and main.render_template("{{ name.upper() }}", {"name": "x"}) == "X"


def test_json_escape_applies_to_expression_results():
    out = main.render_template('{"v": "{{ name + \'"quoted"\' }}"}', {"name": 'say '}, json_escape=True)
    assert json.loads(out) == {"v": 'say "quoted"'}


def test_expression_errors_name_the_expression():
    with pytest.raises(ValueError, match="Expression error in .*price / 0.*division by zero"):
        main.render_template("{{ price / 0 }}", DATA)


def test_credentials_are_not_reachable_from_expressions(client):
    client.put("/api/credentials/KEY", json={"value": "topsecretvalue99"})
    nodes = [node("t", "trigger"), node("i", "input", json='{"a": 1}'), node("m", "template", template="{{ cred.KEY + 'x' }}"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "m"), edge("m", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "failed" and "topsecretvalue99" not in json.dumps(res)
    nodes[2] = node("m", "template", template="Bearer {{cred.KEY}}")  # the normal credential syntax still works and is hidden
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "m"), edge("m", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success" and "topsecretvalue99" not in json.dumps(res)


def test_data_cannot_smuggle_an_expression_in(client):
    nodes = [node("t", "trigger"), node("i", "input", json=json.dumps({"evil": "{{ 1 + 1 }}", "n": 5})), node("m", "template", template="{{evil}} / {{ n * 2 }}"), node("o", "output")]
    res = client.post(f"/api/workflows/{make_flow(client, nodes, [edge('t', 'i'), edge('i', 'm'), edge('m', 'o')])}/run").json()
    assert res["output"] == "{{ 1 + 1 }} / 10"  # incoming data is never re-interpreted


def test_expressions_work_in_set_fields_http_and_conditions_inputs(client, fake_http):
    nodes = [node("t", "trigger"), node("i", "input", json='{"price": 10, "qty": 3, "name": "ada"}'),
             node("s", "set", json='{"total": {{ price * qty }}, "label": "{{ name.title() }}", "big": {{ "true" if price * qty > 20 else "false" }}}'),
             node("h", "http", url="https://api.example.com/items/{{ total + 1 }}", method="GET"), node("o", "output")]
    wid = make_flow(client, nodes, [edge("t", "i"), edge("i", "s"), edge("s", "h"), edge("h", "o")])
    res = client.post(f"/api/workflows/{wid}/run").json()
    assert res["status"] == "success"
    assert next(s for s in res["steps"] if s["node_id"] == "s")["output"] == {"total": 30, "label": "Ada", "big": True}
    assert fake_http.calls[-1]["url"].endswith("/items/31")


def test_a_failing_expression_fails_the_node_cleanly(client):
    nodes = [node("t", "trigger"), node("i", "input", json='{"n": 0}'), node("m", "template", template="{{ 10 / n }}"), node("o", "output")]
    res = client.post(f"/api/workflows/{make_flow(client, nodes, [edge('t', 'i'), edge('i', 'm'), edge('m', 'o')])}/run").json()
    assert res["status"] == "failed" and "division by zero" in res["error"]
