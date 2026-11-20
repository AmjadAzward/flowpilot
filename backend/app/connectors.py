"""Declarative connectors: describe a REST API in a small JSON manifest and call it from the App Action node, without writing code.

Manifest shape::

    {"label": "Acme CRM", "base_url": "https://api.acme.example/v1",
     "auth": {"type": "bearer"},                       # none | bearer | basic | header (name) | query (name)
     "operations": {"get_contact": {"label": "Get contact", "method": "GET", "path": "/contacts/{id}",
                                    "query": {"expand": "{expand}"}, "body": {"note": "{note}"},
                                    "required": ["id"], "result": "data"}}}

{name} placeholders are filled from the node's fields. Path values are URL-encoded. In a query or body a value that is exactly
"{name}" keeps the field's own type; a missing optional field drops that key. This module only validates and builds requests;
sending them (and the address safety checks) stays in main.py.
"""
import re
from typing import Any
from urllib.parse import quote, urlparse

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
OP_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,39})\}")
HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
BLOCKED = {"host", "content-length", "transfer-encoding", "connection", "upgrade", "te", "trailer", "proxy-authorization", "proxy-connection"}
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
AUTH_TYPES = ("none", "bearer", "basic", "header", "query")
MAX_OPERATIONS = 40
MAX_MANIFEST = 40_000


def _template_ok(value: Any, depth: int = 0) -> None:
    if depth > 4: raise ValueError("Bodies can nest at most four levels")
    if isinstance(value, dict):
        if len(value) > 40: raise ValueError("A body or query can have at most 40 entries")
        for k, v in value.items():
            if not isinstance(k, str) or not 1 <= len(k) <= 100: raise ValueError("Keys must be text of 1 to 100 characters")
            _template_ok(v, depth + 1)
    elif isinstance(value, list):
        if len(value) > 40: raise ValueError("A list can have at most 40 entries")
        for v in value: _template_ok(v, depth + 1)
    elif isinstance(value, str):
        if len(value) > 2000: raise ValueError("A text value is too long")
    elif not (value is None or isinstance(value, (bool, int, float))): raise ValueError("Unsupported value in the manifest")


def validate_manifest(raw: Any) -> dict:
    """Return a cleaned manifest or raise ValueError saying what is wrong."""
    if not isinstance(raw, dict): raise ValueError("A connector manifest is a JSON object")
    label = str(raw.get("label") or "").strip()
    if not 1 <= len(label) <= 60: raise ValueError("label is required (up to 60 characters)")
    base = str(raw.get("base_url") or "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443):
        raise ValueError("base_url must be an https address without a login, query or port, like https://api.example.com/v1")
    if ".." in parsed.path or "//" in parsed.path or len(base) > 200: raise ValueError("base_url has an unsafe path")
    auth = raw.get("auth") or {"type": "none"}
    if not isinstance(auth, dict) or auth.get("type", "none") not in AUTH_TYPES: raise ValueError(f"auth.type must be one of {', '.join(AUTH_TYPES)}")
    kind, name = auth.get("type", "none"), str(auth.get("name") or "").strip()
    if kind == "header" and (not HEADER_RE.fullmatch(name) or name.lower() in BLOCKED): raise ValueError("auth.name must be a valid header name")
    if kind == "query" and not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name): raise ValueError("auth.name must be the query parameter name")
    ops_in = raw.get("operations")
    if not isinstance(ops_in, dict) or not 1 <= len(ops_in) <= MAX_OPERATIONS: raise ValueError(f"operations needs between 1 and {MAX_OPERATIONS} entries")
    ops: dict[str, dict] = {}
    for key, op in ops_in.items():
        if not OP_RE.fullmatch(str(key)): raise ValueError(f"Operation name {str(key)[:30]!r}: use lowercase letters, numbers and _")
        if not isinstance(op, dict): raise ValueError(f"Operation {key} must be an object")
        method = str(op.get("method") or "GET").upper()
        if method not in METHODS: raise ValueError(f"Operation {key}: method must be one of {', '.join(METHODS)}")
        path = str(op.get("path") or "")
        if not path.startswith("/") or len(path) > 200 or re.search(r"\.\.|//|[?#\\\s]", path): raise ValueError(f"Operation {key}: path must start with / and contain no .., //, ? or #")
        if re.search(r"[{}]", re.sub(PLACEHOLDER, "", path)): raise ValueError(f"Operation {key}: path placeholders look like {{id}}")
        query, body = op.get("query") or {}, op.get("body")
        if not isinstance(query, dict): raise ValueError(f"Operation {key}: query must be an object")
        _template_ok(query)
        if any(isinstance(v, (dict, list)) for v in query.values()): raise ValueError(f"Operation {key}: query values must be plain values")
        if body is not None:
            _template_ok(body)
            if method == "GET": raise ValueError(f"Operation {key}: a GET request cannot have a body")
        required = op.get("required") or []
        if not isinstance(required, list) or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", x) for x in required): raise ValueError(f"Operation {key}: required is a list of field names")
        result = str(op.get("result") or "")
        if len(result) > 100 or not re.fullmatch(r"[A-Za-z0-9_.-]*", result): raise ValueError(f"Operation {key}: result is a dotted path like data.items")
        ops[key] = {"label": str(op.get("label") or key)[:60], "method": method, "path": path, "query": query, "required": required, "result": result,
                    **({"body": body} if body is not None else {}), "example": op.get("example") if isinstance(op.get("example"), dict) else {}}
    clean = {"label": label, "base_url": base, "auth": {"type": kind, **({"name": name} if name else {})}, "operations": ops}
    if len(str(clean)) > MAX_MANIFEST: raise ValueError("The manifest is too large")
    return clean


def _fill_text(text: str, fields: dict, quoter=None) -> tuple[str, bool]:
    """Fill {name} placeholders. Returns the text and whether every placeholder had a value."""
    complete = True

    def one(m: re.Match) -> str:
        nonlocal complete
        value = fields.get(m.group(1))
        if value is None or value == "": complete = False; return ""
        text_value = str(value).lower() if isinstance(value, bool) else str(value)
        return quoter(text_value) if quoter else text_value
    return PLACEHOLDER.sub(one, text), complete


_missing = object()


def _fill(value: Any, fields: dict) -> Any:
    """Fill a body/query template. A missing field removes its key (or list item) instead of sending an empty value."""
    if isinstance(value, str):
        whole = PLACEHOLDER.fullmatch(value)
        if whole:
            got = fields.get(whole.group(1))
            return _missing if got is None or got == "" else got
        text, ok = _fill_text(value, fields)
        return text if ok else _missing
    if isinstance(value, dict):
        out = {k: _fill(v, fields) for k, v in value.items()}
        return {k: v for k, v in out.items() if v is not _missing}
    if isinstance(value, list):
        return [x for x in (_fill(v, fields) for v in value) if x is not _missing]
    return value


def build_request(manifest: dict, op_name: str, fields: dict) -> dict:
    """Turn an operation plus the node's fields into method, url, query and body. The caller sends it after its address checks."""
    ops = manifest["operations"]
    if op_name not in ops: raise ValueError(f"This connector has no action {op_name or '(none)'}")
    op = ops[op_name]
    for name in op["required"]:
        if fields.get(name) in (None, ""): raise ValueError(f"{name} is required for {op['label']}")
    path, complete = _fill_text(op["path"], fields, quoter=lambda s: quote(s, safe=""))
    if not complete: raise ValueError(f"{op['label']} needs every value used in its path")
    query = _fill(op["query"], fields)
    for k, v in list(query.items()):
        query[k] = str(v).lower() if isinstance(v, bool) else v
    out = {"method": op["method"], "url": manifest["base_url"] + path, "params": query, "result": op["result"]}
    if "body" in op: out["json"] = _fill(op["body"], fields)
    return out
