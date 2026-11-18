"""FlowPilot: small self-hosted visual automation API. SQLite, no cloud required."""
from __future__ import annotations
import copy
import hashlib
import hmac
import json
import os
import secrets
import re
import time
import sqlite3
import socket
import ipaddress
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("FLOWPILOT_DB", str(BASE / "flowpilot.db")))
MAX_NODES = 60
MAX_DELAY_SECONDS = 5.0

app = FastAPI(title="FlowPilot API", version="0.2.0", description="Local visual automation engine")
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

def initialize():
    with conn() as db:
        db.execute("CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', definition TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS executions (id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, output TEXT, steps TEXT, error TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)")
        if not db.execute("SELECT id FROM workflows LIMIT 1").fetchone():
            obj = sample_workflow()
            ts = now()
            db.execute("INSERT INTO workflows VALUES (?,?,?,?,?,?)", (obj["id"], obj["name"], obj["description"], json.dumps(obj), ts, ts))

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
    valid = {"trigger", "input", "transform", "condition", "output", "http", "note", "template", "delay"}
    if any(n.get("type") not in valid for n in body.nodes): raise HTTPException(400, "Unknown node type")
    for e in body.edges:
        if e.get("source") not in ids or e.get("target") not in ids:
            raise HTTPException(400, "Edges must reference existing nodes")


def workflow_row(row):
    result = json.loads(row["definition"])
    result.update(id=row["id"], name=row["name"], description=row["description"], updated_at=row["updated_at"])
    return result

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
    if path.startswith("/api/") and path not in PUBLIC_PATHS and request.method != "OPTIONS":
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
def health(): return {"status":"ok", "version":"0.2.0", "mode":"local"}

@app.get("/api/workflows")
def workflows():
    with conn() as db:
        return [{"id":r["id"], "name":r["name"], "description":r["description"], "updated_at":r["updated_at"]} for r in db.execute("SELECT * FROM workflows ORDER BY updated_at DESC")]

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
    timestamp = now()
    with conn() as db:
        db.execute("INSERT INTO workflows VALUES (?,?,?,?,?,?)", (workflow_id,body.name,body.description,json.dumps(doc),timestamp,timestamp))
    return {"id":workflow_id}

@app.put("/api/workflows/{workflow_id}")
def update_workflow(workflow_id: str, body: WorkflowIn):
    validate_flow(body)
    doc = body.model_dump()
    doc["id"] = workflow_id
    with conn() as db:
        row = db.execute("SELECT id FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if not row: raise HTTPException(404, "Workflow not found")
        db.execute("UPDATE workflows SET name=?,description=?,definition=?,updated_at=? WHERE id=?", (body.name,body.description,json.dumps(doc),now(),workflow_id))
    return {"id":workflow_id, "saved":True}

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
    doc = {**source, "id": new_id, "name": name}
    doc.pop("updated_at", None)
    timestamp = now()
    with conn() as db:
        db.execute("INSERT INTO workflows VALUES (?,?,?,?,?,?)", (new_id, name, source["description"], json.dumps(doc), timestamp, timestamp))
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


def step_action(node: dict, previous: Any):
    """Run one node. Returns (displayed_output, value_passed_downstream, branch)."""
    typ = node["type"]
    d = node.get("data", {})
    if typ in ("trigger", "note", "output"): return previous, previous, None
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
        if method != "GET": raise ValueError("Starter release only supports GET HTTP actions")
        url = safe_url(str(d.get("url", "")))
        # Never follow redirects, so a public URL cannot bounce to an internal address.
        with httpx.Client(timeout=8.0, follow_redirects=False, trust_env=False) as client:
            response = client.get(url)
            response.raise_for_status()
            if len(response.content) > 1_000_000: raise ValueError("HTTP response exceeds 1 MB")
            try: value = response.json()
            except ValueError: value = response.text
            return value, value, None
    raise ValueError(f"Unsupported node: {typ}")


def run_flow(doc: dict):
    nodes = {n["id"]: n for n in doc["nodes"]}
    if not nodes: raise ValueError("Workflow has no nodes")
    incoming = {i: [] for i in nodes}
    outgoing = {i: [] for i in nodes}
    for e in doc["edges"]:
        if e["source"] in nodes and e["target"] in nodes:
            incoming[e["target"]].append(e["source"])
            outgoing[e["source"]].append(e)
    triggers = [nid for nid, n in nodes.items() if n["type"] == "trigger"]
    if len(triggers) != 1: raise ValueError("Workflow must contain exactly one Manual Trigger")
    if incoming[triggers[0]]: raise ValueError("Manual Trigger cannot have incoming connections")
    if any(len(incoming[n]) > 1 for n in nodes): raise ValueError("Engine supports only one incoming connection per node")
    visited, steps = set(), []
    final = {"value": None}
    stack = [(triggers[0], None)]
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

@app.post("/api/workflows/{workflow_id}/run")
def run_workflow(workflow_id: str):
    doc=get_workflow(workflow_id)
    exec_id=str(uuid.uuid4())
    started=now()
    try: result=run_flow(doc)
    except Exception as exc: result={"status":"failed", "output":None, "steps":[], "error":str(exc)}
    with conn() as db:
        db.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?,?)", (exec_id,workflow_id,result["status"],started,now(),json.dumps(result["output"]),json.dumps(result["steps"]),result["error"]))
    return {"id":exec_id, "workflow_id":workflow_id, **result}

@app.get("/api/executions")
def executions(workflow_id: str | None = None):
    with conn() as db:
        if workflow_id:
            rows=db.execute("SELECT * FROM executions WHERE workflow_id=? ORDER BY started_at DESC LIMIT 100",(workflow_id,)).fetchall()
        else: rows=db.execute("SELECT * FROM executions ORDER BY started_at DESC LIMIT 100").fetchall()
    return [{"id":r["id"],"workflow_id":r["workflow_id"],"status":r["status"],"started_at":r["started_at"],"error":r["error"],"steps":json.loads(r["steps"] or "[]"),"output":json.loads(r["output"] or "null")} for r in rows]
