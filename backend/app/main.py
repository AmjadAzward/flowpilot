"""FlowPilot: small self-hosted visual automation API. SQLite, no cloud required."""
from __future__ import annotations
import copy
import hashlib
import hmac
import json
import os
import logging
import secrets
import threading
import re
import time
import sqlite3
import socket
import ipaddress
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("FLOWPILOT_DB", str(BASE / "flowpilot.db")))
MAX_NODES = 60
MAX_DELAY_SECONDS = 5.0
START_TYPES = {"trigger", "schedule", "webhook"}
SCHEDULER_ENABLED = os.getenv("FLOWPILOT_SCHEDULER", "1") != "0"
SCHEDULER_TICK_SECONDS = 10
WEBHOOK_MAX_BODY = 256 * 1024
WEBHOOK_RATE_PER_MINUTE = 60
log = logging.getLogger("flowpilot")


@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    if SCHEDULER_ENABLED:
        threading.Thread(target=scheduler_loop, args=(stop,), daemon=True, name="flowpilot-scheduler").start()
    yield
    stop.set()


app = FastAPI(title="FlowPilot API", version="0.3.0", description="Local visual automation engine", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=False, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])


def now(): return datetime.now(timezone.utc).isoformat()

def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def sample_workflow():
    return {
        "id": "demo-workflow", "name": "My first automation", "description": "Run a sample data transformation and view its output.",
        "nodes": [
            {"id":"trigger","type":"trigger","position":{"x":70,"y":185},"data":{"label":"Manual Trigger"}},
            {"id":"data","type":"input","position":{"x":340,"y":185},"data":{"label":"Sample Input","json":"{\"customer\":\"FITS Express\",\"shipment\":\"FP-1001\",\"status\":\"In Transit\"}"}},
            {"id":"transform","type":"transform","position":{"x":620,"y":185},"data":{"label":"Extract Field","path":"status"}},
            {"id":"output","type":"output","position":{"x":900,"y":185},"data":{"label":"View Result"}}
        ],
        "edges": [
            {"id":"e1","source":"trigger","target":"data"},
            {"id":"e2","source":"data","target":"transform"},
            {"id":"e3","source":"transform","target":"output"}
        ]
    }

def new_token() -> str:
    return secrets.token_urlsafe(24)


def insert_workflow(db, wid: str, name: str, description: str, doc: dict, ts: str):
    db.execute("INSERT INTO workflows (id,name,description,definition,created_at,updated_at,webhook_token) VALUES (?,?,?,?,?,?,?)",
               (wid, name, description, json.dumps(doc), ts, ts, new_token()))


def initialize():
    with conn() as db:
        db.execute("CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', definition TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS executions (id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, output TEXT, steps TEXT, error TEXT)")
        wf_cols = {r["name"] for r in db.execute("PRAGMA table_info(workflows)")}
        if "active" not in wf_cols: db.execute("ALTER TABLE workflows ADD COLUMN active INTEGER NOT NULL DEFAULT 0")
        if "webhook_token" not in wf_cols: db.execute("ALTER TABLE workflows ADD COLUMN webhook_token TEXT")
        if "next_run_at" not in wf_cols: db.execute("ALTER TABLE workflows ADD COLUMN next_run_at TEXT")
        if "trigger_kind" not in {r["name"] for r in db.execute("PRAGMA table_info(executions)")}:
            db.execute("ALTER TABLE executions ADD COLUMN trigger_kind TEXT NOT NULL DEFAULT 'manual'")
        for r in db.execute("SELECT id FROM workflows WHERE webhook_token IS NULL").fetchall():
            db.execute("UPDATE workflows SET webhook_token=? WHERE id=?", (new_token(), r["id"]))
        db.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)")
        if not db.execute("SELECT id FROM workflows LIMIT 1").fetchone():
            obj = sample_workflow()
            ts = now()
            insert_workflow(db, obj["id"], obj["name"], obj["description"], obj, ts)

initialize()

class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


def validate_flow(body: WorkflowIn):
    if len(body.nodes) > MAX_NODES: raise HTTPException(400, f"Maximum {MAX_NODES} nodes per workflow")
    ids = [n.get("id") for n in body.nodes]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise HTTPException(400, "Node IDs must be unique nonempty strings")
    valid = START_TYPES | {"input", "transform", "condition", "output", "http", "note", "template", "delay"}
    if any(n.get("type") not in valid for n in body.nodes): raise HTTPException(400, "Unknown node type")
    for e in body.edges:
        if e.get("source") not in ids or e.get("target") not in ids:
            raise HTTPException(400, "Edges must reference existing nodes")


def workflow_row(row):
    result = json.loads(row["definition"])
    result.update(id=row["id"], name=row["name"], description=row["description"], updated_at=row["updated_at"],
                  active=bool(row["active"]), webhook_token=row["webhook_token"], next_run_at=row["next_run_at"])
    result["trigger"] = trigger_kind(result)
    return result


def trigger_kind(doc: dict) -> str:
    kinds = [n.get("type") for n in doc.get("nodes", []) if n.get("type") in START_TYPES]
    if len(kinds) == 1 and kinds[0] in ("schedule", "webhook"): return kinds[0]
    return "manual"

# ---------------------------------------------------------------------------
# Authentication: one owner account, PBKDF2 password hashing, server-side sessions
# ---------------------------------------------------------------------------
COOKIE = "fp_session"
SESSION_DAYS = 7
PBKDF2_ROUNDS = 240_000
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300
PUBLIC_PATHS = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/auth/logout"}
_failed_logins: dict[str, list[float]] = {}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(check.hex(), digest)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = hash_password("not-a-real-password")  # keeps login timing similar for unknown users


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: str, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc)
    expires = created.timestamp() + SESSION_DAYS * 86400
    with conn() as db:
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (created.isoformat(),))
        db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (token_hash(token), user_id, created.isoformat(), datetime.fromtimestamp(expires, timezone.utc).isoformat()))
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict", path="/")


def session_username(token: str | None) -> str | None:
    if not token: return None
    with conn() as db:
        row = db.execute(
            "SELECT u.username FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=? AND s.expires_at > ?",
            (token_hash(token), now())).fetchone()
    return row["username"] if row else None


class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_PATHS and not path.startswith("/api/hooks/") and request.method != "OPTIONS":
        if not session_username(request.cookies.get(COOKIE)):
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
    return await call_next(request)


@app.get("/api/auth/status")
def auth_status(request: Request):
    with conn() as db:
        has_users = db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    username = session_username(request.cookies.get(COOKIE))
    return {"setup_required": not has_users, "authenticated": username is not None, "username": username}


@app.post("/api/auth/setup", status_code=201)
def auth_setup(body: SetupIn, response: Response):
    """Create the owner account. Only possible while no account exists."""
    user_id = str(uuid.uuid4())
    with conn() as db:
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "An account already exists. Please sign in.")
        db.execute("INSERT INTO users VALUES (?,?,?,?)", (user_id, body.username.lower(), hash_password(body.password), now()))
    create_session(user_id, response)
    return {"username": body.username.lower()}


@app.post("/api/auth/login")
def auth_login(body: LoginIn, request: Request, response: Response):
    key = request.client.host if request.client else "unknown"
    recent = [t for t in _failed_logins.get(key, []) if time.time() - t < LOCKOUT_SECONDS]
    _failed_logins[key] = recent
    if len(recent) >= MAX_FAILED_LOGINS:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    with conn() as db:
        row = db.execute("SELECT id, password_hash FROM users WHERE username=?", (body.username.strip().lower(),)).fetchone()
    ok = verify_password(body.password, row["password_hash"] if row else DUMMY_HASH)
    if not row or not ok:
        _failed_logins[key].append(time.time())
        raise HTTPException(401, "Incorrect username or password")
    _failed_logins.pop(key, None)
    create_session(row["id"], response)
    return {"username": body.username.strip().lower()}


@app.post("/api/auth/logout", status_code=204)
def auth_logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with conn() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
    response.delete_cookie(COOKIE, path="/")


@app.get("/api/health")
def health(): return {"status":"ok", "version":"0.3.0", "mode":"local"}

@app.get("/api/workflows")
def workflows():
    with conn() as db:
        rows = db.execute("SELECT * FROM workflows ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        doc = json.loads(r["definition"])
        out.append({"id": r["id"], "name": r["name"], "description": r["description"], "updated_at": r["updated_at"],
                    "active": bool(r["active"]), "trigger": trigger_kind(doc), "next_run_at": r["next_run_at"]})
    return out

@app.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: str):
    with conn() as db:
        row = db.execute("SELECT * FROM workflows WHERE id=?",(workflow_id,)).fetchone()
    if not row: raise HTTPException(404, "Workflow not found")
    return workflow_row(row)

@app.post("/api/workflows", status_code=201)
def create_workflow(body: WorkflowIn):
    validate_flow(body)
    workflow_id = str(uuid.uuid4())
    doc = body.model_dump()
    doc["id"] = workflow_id
    with conn() as db:
        insert_workflow(db, workflow_id, body.name, body.description, doc, now())
    return {"id":workflow_id}

@app.put("/api/workflows/{workflow_id}")
def update_workflow(workflow_id: str, body: WorkflowIn):
    validate_flow(body)
    doc = body.model_dump()
    doc["id"] = workflow_id
    deactivated = None
    with conn() as db:
        row = db.execute("SELECT active FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if not row: raise HTTPException(404, "Workflow not found")
        active, next_run = bool(row["active"]), None
        if active:
            try: next_run = activation_next_run(doc)
            except ValueError as exc: active, deactivated = False, str(exc)
        db.execute("UPDATE workflows SET name=?,description=?,definition=?,updated_at=?,active=?,next_run_at=? WHERE id=?",
                   (body.name, body.description, json.dumps(doc), now(), int(active), next_run, workflow_id))
    return {"id": workflow_id, "saved": True, "active": active, "deactivated_reason": deactivated}

@app.delete("/api/workflows/{workflow_id}", status_code=204)
def delete_workflow(workflow_id: str):
    with conn() as db:
        db.execute("DELETE FROM workflows WHERE id=?",(workflow_id,))
        db.execute("DELETE FROM executions WHERE workflow_id=?",(workflow_id,))


@app.post("/api/workflows/{workflow_id}/duplicate", status_code=201)
def duplicate_workflow(workflow_id: str):
    source = get_workflow(workflow_id)
    new_id = str(uuid.uuid4())
    name = f"{source['name']} (copy)"[:120]
    doc = {k: v for k, v in source.items() if k not in ("updated_at", "active", "webhook_token", "next_run_at", "trigger")}
    doc.update(id=new_id, name=name)
    with conn() as db:
        insert_workflow(db, new_id, name, source["description"], doc, now())
    return {"id": new_id, "name": name}


@app.delete("/api/executions", status_code=204)
def clear_executions(workflow_id: str | None = None):
    with conn() as db:
        if workflow_id: db.execute("DELETE FROM executions WHERE workflow_id=?", (workflow_id,))
        else: db.execute("DELETE FROM executions")


def read_json(text: str):
    try: return json.loads(text)
    except (ValueError, TypeError) as exc: raise ValueError(f"Invalid JSON input: {exc}") from exc


def get_path(value: Any, path: str):
    if not path: return value
    for key in path.split("."):
        if isinstance(value, dict): value = value.get(key)
        elif isinstance(value, list) and key.isdigit():
            idx = int(key)
            value = value[idx] if 0 <= idx < len(value) else None
        else: return None
    return value


def safe_url(value: str):
    """Only allow HTTPS public destinations; block local/cloud metadata and redirects."""
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError("HTTP node requires a public HTTPS URL on port 443")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "metadata.google.internal"} or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Local/private addresses are blocked")
    try:
        answers = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(item[4][0]).is_global for item in answers):
            raise ValueError("Only public IP addresses are allowed")
    except socket.gaierror as exc: raise ValueError("Cannot resolve destination host") from exc
    return value


def compare(actual: Any, op: str, expected: str) -> bool:
    """Evaluate a Compare Value node. Numeric operators coerce both sides to floats."""
    if op == "exists": return actual is not None
    if op == "not_exists": return actual is None
    a_text = "" if actual is None else str(actual)
    if op == "equals": return a_text.lower() == expected.lower()
    if op == "not_equals": return a_text.lower() != expected.lower()
    if op == "contains": return expected.lower() in a_text.lower()
    if op in ("gt", "lt"):
        try: left, right = float(actual), float(expected)
        except (TypeError, ValueError): return False
        return left > right if op == "gt" else left < right
    raise ValueError(f"Unknown comparison operator: {op}")


def render_template(text: str, data: Any) -> str:
    """Replace {{path}} placeholders using the incoming data."""
    def repl(match):
        value = get_path(data, match.group(1).strip())
        if value is None: return ""
        return value if isinstance(value, str) else json.dumps(value)
    return re.sub(r"\{\{(.*?)\}\}", repl, text)


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
BLOCKED_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "upgrade", "te", "trailer", "proxy-authorization", "proxy-connection"}


def parse_headers(text: str) -> dict[str, str]:
    """Parse the HTTP node's headers field: a JSON object of header names to values."""
    if not text.strip(): return {}
    raw = read_json(text)
    if not isinstance(raw, dict): raise ValueError('Headers must be a JSON object, for example {"Authorization": "Bearer token"}')
    if len(raw) > 20: raise ValueError("At most 20 headers are allowed")
    out = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name): raise ValueError(f"Invalid header name: {name!r}")
        if name.lower() in BLOCKED_HEADERS: raise ValueError(f"Header not allowed: {name}")
        if isinstance(value, (dict, list)) or value is None: raise ValueError(f"Header {name} must be text or a number")
        text_value = str(value)
        if len(text_value) > 2000 or "\r" in text_value or "\n" in text_value: raise ValueError(f"Invalid value for header {name}")
        out[name] = text_value
    return out


def step_action(node: dict, previous: Any):
    """Run one node. Returns (displayed_output, value_passed_downstream, branch)."""
    typ = node["type"]
    d = node.get("data", {})
    if typ in START_TYPES or typ in ("note", "output"): return previous, previous, None
    if typ == "input":
        value = read_json(d.get("json", "{}"))
        return value, value, None
    if typ == "transform":
        value = get_path(previous, str(d.get("path", "")))
        return value, value, None
    if typ == "template":
        value = render_template(str(d.get("template", "")), previous)
        return value, value, None
    if typ == "delay":
        try: seconds = float(d.get("seconds", 1))
        except (TypeError, ValueError): raise ValueError("Delay seconds must be a number")
        seconds = max(0.0, min(seconds, MAX_DELAY_SECONDS))
        time.sleep(seconds)
        return {"waited_seconds": seconds}, previous, None
    if typ == "condition":
        actual = get_path(previous, str(d.get("path", "")))
        matched = compare(actual, str(d.get("op", "equals")), str(d.get("equals", "")))
        return {"matched": matched, "value": previous}, previous, "true" if matched else "false"
    if typ == "http":
        method = str(d.get("method", "GET")).upper()
        if method not in HTTP_METHODS: raise ValueError(f"Unsupported HTTP method: {method}")
        url = safe_url(render_template(str(d.get("url", "")), previous))
        headers = {k: render_template(v, previous) for k, v in parse_headers(str(d.get("headers", ""))).items()}
        kwargs: dict[str, Any] = {}
        body_text = render_template(str(d.get("body", "")), previous) if method in ("POST", "PUT", "PATCH") else ""
        if body_text.strip():
            if len(body_text) > 1_000_000: raise ValueError("Request body exceeds 1 MB")
            try: kwargs["json"] = json.loads(body_text)
            except ValueError:
                kwargs["content"] = body_text.encode()
                if not any(k.lower() == "content-type" for k in headers): headers["Content-Type"] = "text/plain; charset=utf-8"
        # Never follow redirects, so a public URL cannot bounce to an internal address.
        with httpx.Client(timeout=8.0, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            if len(response.content) > 1_000_000: raise ValueError("HTTP response exceeds 1 MB")
            try: value = response.json()
            except ValueError: value = response.text
            return value, value, None
    raise ValueError(f"Unsupported node: {typ}")


def run_flow(doc: dict, initial: Any = None):
    nodes = {n["id"]: n for n in doc["nodes"]}
    if not nodes: raise ValueError("Workflow has no nodes")
    incoming = {i: [] for i in nodes}
    outgoing = {i: [] for i in nodes}
    for e in doc["edges"]:
        if e["source"] in nodes and e["target"] in nodes:
            incoming[e["target"]].append(e["source"])
            outgoing[e["source"]].append(e)
    triggers = [nid for nid, n in nodes.items() if n["type"] in START_TYPES]
    if len(triggers) != 1: raise ValueError("Workflow must contain exactly one trigger (Manual, Schedule or Webhook)")
    if incoming[triggers[0]]: raise ValueError("A trigger cannot have incoming connections")
    if any(len(incoming[n]) > 1 for n in nodes): raise ValueError("Engine supports only one incoming connection per node")
    visited, steps = set(), []
    final = {"value": None}
    stack = [(triggers[0], initial)]
    while stack:
        cursor, previous = stack.pop()
        if cursor in visited: raise ValueError("Cycles are not supported in this release")
        visited.add(cursor)
        if len(steps) >= MAX_NODES: raise ValueError("Maximum execution steps exceeded")
        node = nodes[cursor]
        label = node.get("data", {}).get("label", node["type"])
        t0 = time.perf_counter()
        try:
            shown, passed, branch = step_action(node, previous)
        except Exception as exc:
            steps.append({"node_id": cursor, "label": label, "type": node["type"], "status": "error", "error": str(exc), "duration_ms": round((time.perf_counter() - t0) * 1000, 1)})
            return {"status": "failed", "output": None, "steps": steps, "error": str(exc)}
        steps.append({"node_id": cursor, "label": label, "type": node["type"], "status": "success", "output": shown, "branch": branch, "duration_ms": round((time.perf_counter() - t0) * 1000, 1)})
        final["value"] = passed if node["type"] == "condition" else shown
        # Condition nodes only follow the matching branch; edges without a handle always follow.
        followed = [e for e in outgoing[cursor] if branch is None or e.get("sourceHandle") in (None, branch)]
        for e in reversed(followed): stack.append((e["target"], passed))
    return {"status": "success", "output": final["value"], "steps": steps, "error": None}

def execute_workflow(doc: dict, kind: str, initial: Any = None) -> dict:
    """Run a workflow and store the execution. `initial` may be a callable so input errors are recorded as failed runs."""
    exec_id, started = str(uuid.uuid4()), now()
    try: result = run_flow(doc, initial() if callable(initial) else initial)
    except Exception as exc: result = {"status": "failed", "output": None, "steps": [], "error": str(exc)}
    with conn() as db:
        db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                   (exec_id, doc["id"], result["status"], started, now(), json.dumps(result["output"]), json.dumps(result["steps"]), result["error"], kind))
    return {"id": exec_id, "workflow_id": doc["id"], "trigger": kind, **result}


def start_node(doc: dict) -> dict | None:
    return next((n for n in doc["nodes"] if n.get("type") in START_TYPES), None)


def webhook_payload(method: str, query: dict, body: Any, content_type: str = "") -> dict:
    return {"method": method, "query": query, "body": body, "content_type": content_type}


class RunIn(BaseModel):
    input: Any = None


@app.post("/api/workflows/{workflow_id}/run")
def run_workflow(workflow_id: str, body: RunIn | None = None):
    """Manual run. Schedule and webhook workflows are fed a test event so they can be tried before activating."""
    doc = get_workflow(workflow_id)
    node = start_node(doc)
    override = body.input if body else None

    def initial():
        if override is not None: return override
        data = (node or {}).get("data", {})
        if node and node["type"] == "webhook":
            sample = str(data.get("sample", "")).strip()
            return webhook_payload("POST", {}, read_json(sample) if sample else None, "application/json")
        if node and node["type"] == "schedule": return {"scheduled_at": now(), "manual": True}
        return None
    return execute_workflow(doc, "manual", initial)


# ---------------------------------------------------------------------------
# Activation, schedules and webhooks
# ---------------------------------------------------------------------------
def validate_schedule(data: dict) -> dict:
    mode = str(data.get("mode", "interval"))
    if mode == "interval":
        try: minutes = int(float(data.get("minutes", 15)))
        except (TypeError, ValueError): raise ValueError("Schedule interval must be a number of minutes")
        if not 1 <= minutes <= 10080: raise ValueError("Schedule interval must be between 1 and 10080 minutes")
        return {"mode": "interval", "minutes": minutes}
    if mode == "daily":
        m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(data.get("time", "09:00")).strip())
        if not m: raise ValueError("Daily time must look like 09:30")
        return {"mode": "daily", "hour": int(m.group(1)), "minute": int(m.group(2))}
    raise ValueError(f"Unknown schedule mode: {mode}")


def next_run_after(cfg: dict, after: datetime) -> datetime:
    if cfg["mode"] == "interval": return after + timedelta(minutes=cfg["minutes"])
    local = after.astimezone()  # daily times are in the computer's local time zone
    candidate = local.replace(hour=cfg["hour"], minute=cfg["minute"], second=0, microsecond=0)
    if candidate <= local: candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def activation_next_run(doc: dict, after: datetime | None = None) -> str | None:
    """Validate that a workflow can be active and return its first run time (None for webhooks)."""
    starts = [n for n in doc["nodes"] if n.get("type") in START_TYPES]
    if len(starts) != 1: raise ValueError("A workflow needs exactly one trigger to be activated")
    node = starts[0]
    if node["type"] == "trigger": raise ValueError("Add a Schedule or Webhook trigger to activate this workflow")
    if node["type"] == "webhook": return None
    cfg = validate_schedule(node.get("data", {}))
    return next_run_after(cfg, after or datetime.now(timezone.utc)).isoformat()


class ActiveIn(BaseModel):
    active: bool


@app.post("/api/workflows/{workflow_id}/active")
def set_active(workflow_id: str, body: ActiveIn):
    doc = get_workflow(workflow_id)
    next_run = None
    if body.active:
        try: next_run = activation_next_run(doc)
        except ValueError as exc: raise HTTPException(400, str(exc))
    with conn() as db:
        db.execute("UPDATE workflows SET active=?, next_run_at=? WHERE id=?", (int(body.active), next_run, workflow_id))
    return {"id": workflow_id, "active": body.active, "next_run_at": next_run}


@app.post("/api/workflows/{workflow_id}/webhook/rotate")
def rotate_webhook(workflow_id: str):
    get_workflow(workflow_id)
    token = new_token()
    with conn() as db:
        db.execute("UPDATE workflows SET webhook_token=? WHERE id=?", (token, workflow_id))
    return {"webhook_token": token}


def scheduler_tick(at: datetime | None = None) -> int:
    """Run every active scheduled workflow that is due. Returns how many were started."""
    moment = at or datetime.now(timezone.utc)
    with conn() as db:
        due = db.execute("SELECT id FROM workflows WHERE active=1 AND next_run_at IS NOT NULL AND next_run_at <= ?", (moment.isoformat(),)).fetchall()
    started = 0
    for row in due:
        try:
            doc = get_workflow(row["id"])
            node = start_node(doc)
            if not node or node["type"] != "schedule":
                with conn() as db: db.execute("UPDATE workflows SET active=0, next_run_at=NULL WHERE id=?", (row["id"],))
                continue
            # Advance first: if the server was off, a missed run happens once and then the normal rhythm resumes.
            upcoming = next_run_after(validate_schedule(node.get("data", {})), moment).isoformat()
            with conn() as db: db.execute("UPDATE workflows SET next_run_at=? WHERE id=?", (upcoming, row["id"]))
            execute_workflow(doc, "schedule", {"scheduled_at": moment.isoformat(), "manual": False})
            started += 1
        except Exception:  # one broken workflow must never stop the scheduler
            log.exception("Scheduled run failed for %s", row["id"])
    return started


def scheduler_loop(stop: threading.Event) -> None:
    while not stop.wait(SCHEDULER_TICK_SECONDS):
        try: scheduler_tick()
        except Exception: log.exception("Scheduler tick failed")


_hook_hits: dict[str, list[float]] = {}


def hook_allowed(token: str) -> bool:
    recent = [t for t in _hook_hits.get(token, []) if time.time() - t < 60]
    if len(recent) >= WEBHOOK_RATE_PER_MINUTE:
        _hook_hits[token] = recent
        return False
    recent.append(time.time())
    _hook_hits[token] = recent
    return True


@app.api_route("/api/hooks/{token}", methods=["GET", "POST"])
async def receive_webhook(token: str, request: Request):
    """Public endpoint: the secret token in the URL is the credential. Only active webhook workflows respond."""
    with conn() as db:
        row = db.execute("SELECT * FROM workflows WHERE webhook_token=? AND active=1", (token,)).fetchone()
    doc = workflow_row(row) if row else None
    if not doc or trigger_kind(doc) != "webhook": raise HTTPException(404, "Not found")
    if not hook_allowed(token): raise HTTPException(429, "Too many requests")
    if int(request.headers.get("content-length") or 0) > WEBHOOK_MAX_BODY: raise HTTPException(413, "Payload too large")
    raw = await request.body()
    if len(raw) > WEBHOOK_MAX_BODY: raise HTTPException(413, "Payload too large")
    ctype = request.headers.get("content-type", "")
    body: Any = None
    if raw:
        text = raw.decode("utf-8", errors="replace")
        try: body = json.loads(text)
        except ValueError: body = text
    payload = webhook_payload(request.method, dict(request.query_params), body, ctype.split(";")[0].strip())
    result = await run_in_threadpool(execute_workflow, doc, "webhook", payload)
    code = 200 if result["status"] == "success" else 500
    return JSONResponse({"status": result["status"], "execution_id": result["id"]}, status_code=code)


@app.get("/api/executions")
def executions(workflow_id: str | None = None):
    with conn() as db:
        if workflow_id:
            rows=db.execute("SELECT * FROM executions WHERE workflow_id=? ORDER BY started_at DESC LIMIT 100",(workflow_id,)).fetchall()
        else: rows=db.execute("SELECT * FROM executions ORDER BY started_at DESC LIMIT 100").fetchall()
    return [{"id":r["id"],"workflow_id":r["workflow_id"],"status":r["status"],"started_at":r["started_at"],"error":r["error"],"trigger":r["trigger_kind"],"steps":json.loads(r["steps"] or "[]"),"output":json.loads(r["output"] or "null")} for r in rows]
