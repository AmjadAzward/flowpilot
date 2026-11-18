"""FlowPilot: small self-hosted visual automation API. SQLite, no cloud required."""
from __future__ import annotations
import base64
import contextvars
import ast
import copy
import operator
import csv
import hashlib
import hmac
import imaplib
import mimetypes
import html as html_lib
import json
import os
import logging
import secrets
import threading
import re
import time
import sqlite3
import socket
import io
import ipaddress
import smtplib
import subprocess
import sys
import tempfile
import ssl
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from email import message_from_bytes, policy as email_policy
from email.message import EmailMessage
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

BASE = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("FLOWPILOT_DB", str(BASE / "flowpilot.db")))
MAX_NODES = 60
MAX_DELAY_SECONDS = 5.0
START_TYPES = {"trigger", "schedule", "webhook", "email_in"}
SCHEDULER_ENABLED = os.getenv("FLOWPILOT_SCHEDULER", "1") != "0"
SCHEDULER_TICK_SECONDS = 10
WEBHOOK_MAX_BODY = 256 * 1024
WEBHOOK_RATE_PER_MINUTE = int(os.getenv("FLOWPILOT_WEBHOOK_RATE", "60"))  # per webhook URL
log = logging.getLogger("flowpilot")


@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    recover_waits()
    if SCHEDULER_ENABLED:
        threading.Thread(target=scheduler_loop, args=(stop,), daemon=True, name="flowpilot-scheduler").start()
    yield
    stop.set()


app = FastAPI(title="FlowPilot API", version="0.10.0", description="Local visual automation engine", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=False, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])


def now(): return datetime.now(timezone.utc).isoformat()

def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA synchronous=NORMAL")  # in WAL mode: no disk sync on every commit (a power cut can lose the last moments, never corrupt the file)
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


def insert_workflow(db, wid: str, name: str, description: str, doc: dict, ts: str, project_id: str | None = None):
    db.execute("INSERT INTO workflows (id,name,description,definition,created_at,updated_at,webhook_token,project_id) VALUES (?,?,?,?,?,?,?,?)",
               (wid, name, description, json.dumps(doc), ts, ts, new_token(), project_id))


def initialize():
    with conn() as db:
        db.execute("PRAGMA journal_mode=WAL")  # readers and writers from the API and worker processes do not block each other
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
        db.execute("CREATE TABLE IF NOT EXISTS credentials (name TEXT PRIMARY KEY, value_enc TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS project_members (project_id TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY (project_id, user_id))")
        if "project_id" not in {r["name"] for r in db.execute("PRAGMA table_info(workflows)")}: db.execute("ALTER TABLE workflows ADD COLUMN project_id TEXT")
        if "role" not in {r["name"] for r in db.execute("PRAGMA table_info(users)")}:
            db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
            first = db.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
            if first: db.execute("UPDATE users SET role='owner' WHERE id=?", (first["id"],))
        db.execute("CREATE TABLE IF NOT EXISTS workflow_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL, saved_at TEXT NOT NULL, saved_by TEXT, name TEXT NOT NULL, definition TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS waits (id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, workflow_id TEXT NOT NULL, node_id TEXT NOT NULL, value TEXT NOT NULL, resume_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS oauth_pending (state TEXT PRIMARY KEY, payload_enc TEXT NOT NULL, created_at REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL, claimed_by TEXT, claimed_at TEXT, finished_at TEXT, execution_id TEXT, response TEXT, error TEXT)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_versions_wf ON workflow_versions (workflow_id, id)")
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
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for t in value:
            t = str(t).strip()
            if not t: continue
            if not re.fullmatch(r"[\w .\-]{1,24}", t): raise ValueError("Tags use letters, numbers, spaces, . _ - (max 24 characters)")
            if t.lower() not in [x.lower() for x in out]: out.append(t)
        if len(out) > 10: raise ValueError("At most 10 tags")
        return out


def validate_flow(body: WorkflowIn):
    if len(body.nodes) > MAX_NODES: raise HTTPException(400, f"Maximum {MAX_NODES} nodes per workflow")
    ids = [n.get("id") for n in body.nodes]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise HTTPException(400, "Node IDs must be unique nonempty strings")
    valid = START_TYPES | {"input", "transform", "condition", "output", "http", "note", "template", "delay",
                           "loop", "merge", "set", "filter", "csv", "notify", "email", "subflow", "switch", "code", "fail", "sql", "rss", "telegram", "github", "wait", "respond", "sheets", "gmail", "sticky", "ai", "repeat", "file"}
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
    if len(kinds) == 1 and kinds[0] in ("schedule", "webhook", "email_in"): return kinds[0]
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


def session_user(token: str | None) -> dict | None:
    if not token: return None
    with conn() as db:
        row = db.execute(
            "SELECT u.id, u.username, u.role FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=? AND s.expires_at > ?",
            (token_hash(token), now())).fetchone()
    return dict(row) if row else None


def session_username(token: str | None) -> str | None:
    user = session_user(token)
    return user["username"] if user else None


def sees_everything(actor: dict | None) -> bool:
    return actor is None or actor["role"] in ("owner", "admin")


def my_project_ids(actor: dict) -> set[str]:
    with conn() as db:
        return {r["project_id"] for r in db.execute("SELECT project_id FROM project_members WHERE user_id=?", (actor["id"],)).fetchall()}


def can_see_workflow(actor: dict | None, workflow_id: str) -> bool:
    """Owners and admins see everything. Others see unassigned workflows and those in projects they belong to."""
    if sees_everything(actor): return True
    with conn() as db:
        row = db.execute("SELECT project_id FROM workflows WHERE id=?", (workflow_id,)).fetchone()
    return row is None or row["project_id"] is None or row["project_id"] in my_project_ids(actor)


def visible_workflow_ids(actor: dict | None) -> set[str] | None:
    """None means 'all'. Otherwise the ids this person may see."""
    if sees_everything(actor): return None
    mine = my_project_ids(actor)
    with conn() as db:
        rows = db.execute("SELECT id, project_id FROM workflows").fetchall()
    return {r["id"] for r in rows if r["project_id"] is None or r["project_id"] in mine}


def check_subflow_access(actor: dict | None, nodes: list[dict]) -> None:
    for n in nodes:
        target = str((n.get("data") or {}).get("workflow_id") or "").strip() if n.get("type") == "subflow" else ""
        if target and not can_see_workflow(actor, target):
            raise HTTPException(403, "You do not have access to the workflow chosen in a Run Workflow node")


ROLES = ("owner", "admin", "editor", "viewer")
ASSIGNABLE_ROLES = ("admin", "editor", "viewer")
SELF_SERVICE = {"/api/auth/password"}


def allowed(role: str, method: str, path: str) -> bool:
    """Roles: viewer reads, editor also builds and runs workflows, admin also manages credentials, owner also manages users."""
    if path in SELF_SERVICE: return True
    if path.startswith(("/api/users", "/api/backup")): return role == "owner"
    if path.startswith("/api/projects") and method not in ("GET", "HEAD"): return role == "owner"
    if method in ("GET", "HEAD"): return True
    if path.startswith(("/api/credentials", "/api/oauth", "/api/settings")): return role in ("owner", "admin")
    return role in ("owner", "admin", "editor")


class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_PATHS and path != "/api/oauth/callback" and not path.startswith("/api/hooks/") and request.method != "OPTIONS":
        user = session_user(request.cookies.get(COOKIE))
        if not user:
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        if not allowed(user["role"], request.method, path):
            return JSONResponse({"detail": "Your role does not allow this action"}, status_code=403)
        request.state.user = user["username"]
        request.state.actor = user
        if user["role"] not in ("owner", "admin"):
            wf = re.match(r"^/api/workflows/([^/]+)", path)
            ex = re.match(r"^/api/executions/([^/]+)/cancel$", path)
            target = wf.group(1) if wf else None
            if ex:
                with conn() as db: row = db.execute("SELECT workflow_id FROM executions WHERE id=?", (ex.group(1),)).fetchone()
                target = row["workflow_id"] if row else None
            if target and not can_see_workflow(user, target):
                return JSONResponse({"detail": "Not found"}, status_code=404)
    return await call_next(request)


@app.get("/api/auth/status")
def auth_status(request: Request):
    with conn() as db:
        has_users = db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    user = session_user(request.cookies.get(COOKIE))
    projects = []
    if user:
        with conn() as db:
            rows = db.execute("SELECT id, name FROM projects ORDER BY name").fetchall()
        mine = None if sees_everything(user) else my_project_ids(user)
        projects = [{"id": r["id"], "name": r["name"]} for r in rows if mine is None or r["id"] in mine]
    return {"setup_required": not has_users, "authenticated": user is not None, "username": user["username"] if user else None, "role": user["role"] if user else None, "projects": projects}


@app.post("/api/auth/setup", status_code=201)
def auth_setup(body: SetupIn, response: Response):
    """Create the owner account. Only possible while no account exists."""
    user_id = str(uuid.uuid4())
    with conn() as db:
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "An account already exists. Please sign in.")
        db.execute("INSERT INTO users (id,username,password_hash,created_at,role) VALUES (?,?,?,?,?)", (user_id, body.username.lower(), hash_password(body.password), now(), "owner"))
    create_session(user_id, response)
    return {"username": body.username.lower(), "role": "owner"}


@app.post("/api/auth/login")
def auth_login(body: LoginIn, request: Request, response: Response):
    key = request.client.host if request.client else "unknown"
    recent = [t for t in _failed_logins.get(key, []) if time.time() - t < LOCKOUT_SECONDS]
    _failed_logins[key] = recent
    if len(recent) >= MAX_FAILED_LOGINS:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    with conn() as db:
        row = db.execute("SELECT id, password_hash, role FROM users WHERE username=?", (body.username.strip().lower(),)).fetchone()
    ok = verify_password(body.password, row["password_hash"] if row else DUMMY_HASH)
    if not row or not ok:
        _failed_logins[key].append(time.time())
        raise HTTPException(401, "Incorrect username or password")
    _failed_logins.pop(key, None)
    create_session(row["id"], response)
    return {"username": body.username.strip().lower(), "role": row["role"]}


@app.post("/api/auth/logout", status_code=204)
def auth_logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with conn() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
    response.delete_cookie(COOKIE, path="/")


# ---------------------------------------------------------------------------
# Users and roles (owner only), and changing your own password
# ---------------------------------------------------------------------------
MAX_USERS = 50


class NewUserIn(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=200)
    role: str = "editor"


class UserPatch(BaseModel):
    role: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)


class PasswordIn(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=8, max_length=200)


@app.get("/api/users")
def list_users():
    with conn() as db:
        rows = db.execute("SELECT id, username, role, created_at FROM users ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/users", status_code=201)
def create_user(body: NewUserIn):
    if body.role not in ASSIGNABLE_ROLES: raise HTTPException(400, "Role must be admin, editor or viewer")
    uid = str(uuid.uuid4())
    with conn() as db:
        if db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] >= MAX_USERS: raise HTTPException(400, f"Maximum {MAX_USERS} users")
        if db.execute("SELECT 1 FROM users WHERE username=?", (body.username.lower(),)).fetchone(): raise HTTPException(409, "That username is taken")
        db.execute("INSERT INTO users (id,username,password_hash,created_at,role) VALUES (?,?,?,?,?)", (uid, body.username.lower(), hash_password(body.password), now(), body.role))
    return {"id": uid, "username": body.username.lower(), "role": body.role}


@app.put("/api/users/{user_id}")
def update_user(user_id: str, body: UserPatch):
    with conn() as db:
        row = db.execute("SELECT id, role FROM users WHERE id=?", (user_id,)).fetchone()
        if not row: raise HTTPException(404, "User not found")
        if row["role"] == "owner": raise HTTPException(400, "The owner account cannot be changed here")
        if body.role is not None:
            if body.role not in ASSIGNABLE_ROLES: raise HTTPException(400, "Role must be admin, editor or viewer")
            db.execute("UPDATE users SET role=? WHERE id=?", (body.role, user_id))
        if body.password is not None:
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.password), user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    return {"id": user_id, "updated": True}


@app.delete("/api/users/{user_id}", status_code=204)
def delete_user(user_id: str):
    with conn() as db:
        row = db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if not row: raise HTTPException(404, "User not found")
        if row["role"] == "owner": raise HTTPException(400, "The owner account cannot be deleted")
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM project_members WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM users WHERE id=?", (user_id,))


@app.post("/api/auth/password")
def change_password(body: PasswordIn, request: Request):
    user = session_user(request.cookies.get(COOKIE))
    if not user: raise HTTPException(401, "Not signed in")
    with conn() as db:
        row = db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not verify_password(body.current, row["password_hash"]): raise HTTPException(400, "Current password is incorrect")
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body.new), user["id"]))
        db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash != ?", (user["id"], token_hash(request.cookies.get(COOKIE) or "")))
    return {"changed": True}


# ---------------------------------------------------------------------------
# Projects: groups of workflows that only their members (plus owners and admins) can see
# ---------------------------------------------------------------------------
MAX_PROJECTS = 100


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    user_ids: list[str] | None = Field(default=None, max_length=200)


class AssignIn(BaseModel):
    project_id: str | None = None


@app.get("/api/projects")
def list_projects(request: Request):
    actor = getattr(request.state, "actor", None)
    mine = None if sees_everything(actor) else my_project_ids(actor)
    with conn() as db:
        rows = db.execute("SELECT id, name, created_at FROM projects ORDER BY name").fetchall()
        members = db.execute("SELECT m.project_id, m.user_id, u.username FROM project_members m JOIN users u ON u.id = m.user_id").fetchall()
        counts = {r["project_id"]: r["c"] for r in db.execute("SELECT project_id, COUNT(*) c FROM workflows WHERE project_id IS NOT NULL GROUP BY project_id").fetchall()}
    out = []
    for r in rows:
        if mine is not None and r["id"] not in mine: continue
        item = {"id": r["id"], "name": r["name"], "created_at": r["created_at"], "workflows": counts.get(r["id"], 0)}
        if mine is None: item["members"] = [{"user_id": m["user_id"], "username": m["username"]} for m in members if m["project_id"] == r["id"]]
        out.append(item)
    return out


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectIn):
    name = body.name.strip()
    pid = str(uuid.uuid4())
    with conn() as db:
        if db.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"] >= MAX_PROJECTS: raise HTTPException(400, f"At most {MAX_PROJECTS} projects")
        if db.execute("SELECT 1 FROM projects WHERE lower(name)=lower(?)", (name,)).fetchone(): raise HTTPException(409, "A project with that name already exists")
        db.execute("INSERT INTO projects (id,name,created_at) VALUES (?,?,?)", (pid, name, now()))
    return {"id": pid, "name": name}


@app.put("/api/projects/assign/{workflow_id}")
def assign_workflow(workflow_id: str, body: AssignIn):
    get_workflow(workflow_id)
    with conn() as db:
        if body.project_id and not db.execute("SELECT 1 FROM projects WHERE id=?", (body.project_id,)).fetchone(): raise HTTPException(404, "Project not found")
        db.execute("UPDATE workflows SET project_id=? WHERE id=?", (body.project_id or None, workflow_id))
    return {"id": workflow_id, "project_id": body.project_id or None}


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectPatch):
    with conn() as db:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone(): raise HTTPException(404, "Project not found")
        if body.name is not None:
            clash = db.execute("SELECT 1 FROM projects WHERE lower(name)=lower(?) AND id != ?", (body.name.strip(), project_id)).fetchone()
            if clash: raise HTTPException(409, "A project with that name already exists")
            db.execute("UPDATE projects SET name=? WHERE id=?", (body.name.strip(), project_id))
        if body.user_ids is not None:
            known = {r["id"] for r in db.execute("SELECT id FROM users").fetchall()}
            if any(u not in known for u in body.user_ids): raise HTTPException(400, "Unknown user")
            db.execute("DELETE FROM project_members WHERE project_id=?", (project_id,))
            for u in set(body.user_ids): db.execute("INSERT INTO project_members (project_id,user_id) VALUES (?,?)", (project_id, u))
    return {"id": project_id, "updated": True}


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str):
    with conn() as db:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone(): raise HTTPException(404, "Project not found")
        if db.execute("SELECT 1 FROM workflows WHERE project_id=? LIMIT 1", (project_id,)).fetchone(): raise HTTPException(400, "Move or delete the project's workflows first")
        db.execute("DELETE FROM project_members WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM projects WHERE id=?", (project_id,))


@app.get("/api/health")
def health(): return {"status":"ok", "version":"0.10.0", "mode":"local"}

@app.get("/api/workflows")
def workflows(request: Request):
    visible = visible_workflow_ids(getattr(request.state, "actor", None))
    with conn() as db:
        rows = db.execute("SELECT * FROM workflows ORDER BY updated_at DESC").fetchall()
        names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM projects").fetchall()}
    out = []
    for r in rows:
        if visible is not None and r["id"] not in visible: continue
        doc = json.loads(r["definition"])
        out.append({"id": r["id"], "name": r["name"], "description": r["description"], "updated_at": r["updated_at"], "tags": doc.get("tags", []),
                    "active": bool(r["active"]), "trigger": trigger_kind(doc), "next_run_at": r["next_run_at"],
                    "project_id": r["project_id"], "project_name": names.get(r["project_id"])})
    return out

@app.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: str):
    with conn() as db:
        row = db.execute("SELECT * FROM workflows WHERE id=?",(workflow_id,)).fetchone()
    if not row: raise HTTPException(404, "Workflow not found")
    return workflow_row(row)

@app.post("/api/workflows", status_code=201)
def create_workflow(body: WorkflowIn, request: Request, project_id: str | None = None):
    validate_flow(body)
    actor = getattr(request.state, "actor", None)
    check_subflow_access(actor, body.nodes)
    if project_id:
        with conn() as db:
            if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone(): raise HTTPException(404, "Project not found")
        if not sees_everything(actor) and project_id not in my_project_ids(actor): raise HTTPException(403, "You are not a member of that project")
    workflow_id = str(uuid.uuid4())
    doc = body.model_dump()
    doc["id"] = workflow_id
    with conn() as db:
        insert_workflow(db, workflow_id, body.name, body.description, doc, now(), project_id or None)
    return {"id":workflow_id}

MAX_VERSIONS = 30


def canonical(doc: dict) -> str:
    return json.dumps({k: doc.get(k) for k in ("name", "description", "nodes", "edges")}, sort_keys=True)


@app.put("/api/workflows/{workflow_id}")
def update_workflow(workflow_id: str, body: WorkflowIn, request: Request):
    validate_flow(body)
    check_subflow_access(getattr(request.state, "actor", None), body.nodes)
    doc = body.model_dump()
    doc["id"] = workflow_id
    deactivated = None
    with conn() as db:
        row = db.execute("SELECT active, definition, name FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if not row: raise HTTPException(404, "Workflow not found")
        if "tags" not in body.model_fields_set: doc["tags"] = json.loads(row["definition"]).get("tags", [])
        if canonical(json.loads(row["definition"])) != canonical(doc):
            who = getattr(getattr(request, "state", None), "user", None)
            db.execute("INSERT INTO workflow_versions (workflow_id,saved_at,saved_by,name,definition) VALUES (?,?,?,?,?)", (workflow_id, now(), who, row["name"], row["definition"]))
            db.execute("DELETE FROM workflow_versions WHERE workflow_id=? AND id NOT IN (SELECT id FROM workflow_versions WHERE workflow_id=? ORDER BY id DESC LIMIT ?)", (workflow_id, workflow_id, MAX_VERSIONS))
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
        db.execute("DELETE FROM workflow_versions WHERE workflow_id=?", (workflow_id,))
        db.execute("DELETE FROM waits WHERE workflow_id=?", (workflow_id,))


@app.get("/api/workflows/{workflow_id}/versions")
def list_versions(workflow_id: str):
    get_workflow(workflow_id)
    with conn() as db:
        rows = db.execute("SELECT id, saved_at, saved_by, name, definition FROM workflow_versions WHERE workflow_id=? ORDER BY id DESC", (workflow_id,)).fetchall()
    return [{"id": r["id"], "saved_at": r["saved_at"], "saved_by": r["saved_by"], "name": r["name"], "nodes": len(json.loads(r["definition"]).get("nodes", []))} for r in rows]


def load_version(workflow_id: str, version_id: int) -> sqlite3.Row:
    with conn() as db:
        row = db.execute("SELECT * FROM workflow_versions WHERE id=? AND workflow_id=?", (version_id, workflow_id)).fetchone()
    if not row: raise HTTPException(404, "Version not found")
    return row


@app.get("/api/workflows/{workflow_id}/versions/{version_id}")
def get_version(workflow_id: str, version_id: int):
    row = load_version(workflow_id, version_id)
    doc = json.loads(row["definition"])
    doc.update(id=workflow_id, version_id=version_id, saved_at=row["saved_at"])
    return doc


@app.post("/api/workflows/{workflow_id}/versions/{version_id}/restore")
def restore_version(workflow_id: str, version_id: int, request: Request):
    """Restore an older version. The current version is kept as a new history entry first, so a restore can be undone."""
    doc = json.loads(load_version(workflow_id, version_id)["definition"])
    body = WorkflowIn(name=doc["name"], description=doc.get("description", ""), nodes=doc["nodes"], edges=doc["edges"])
    return update_workflow(workflow_id, body, request)


@app.post("/api/workflows/{workflow_id}/duplicate", status_code=201)
def duplicate_workflow(workflow_id: str):
    source = get_workflow(workflow_id)
    new_id = str(uuid.uuid4())
    name = f"{source['name']} (copy)"[:120]
    doc = {k: v for k, v in source.items() if k not in ("updated_at", "active", "webhook_token", "next_run_at", "trigger")}
    doc.update(id=new_id, name=name)
    with conn() as db:
        src = db.execute("SELECT project_id FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        insert_workflow(db, new_id, name, source["description"], doc, now(), src["project_id"] if src else None)
    return {"id": new_id, "name": name}


@app.delete("/api/executions", status_code=204)
def clear_executions(request: Request, workflow_id: str | None = None):
    visible = visible_workflow_ids(getattr(request.state, "actor", None))
    with conn() as db:
        if workflow_id:
            if visible is None or workflow_id in visible: db.execute("DELETE FROM executions WHERE workflow_id=?", (workflow_id,))
        elif visible is None: db.execute("DELETE FROM executions")
        else:
            for wid in visible: db.execute("DELETE FROM executions WHERE workflow_id=?", (wid,))
        db.execute("DELETE FROM waits WHERE execution_id NOT IN (SELECT id FROM executions)")


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
    assert_public_host(hostname)
    return value


def assert_public_host(hostname: str, relaxed: bool = False) -> None:
    """Block localhost, private and cloud-metadata addresses. `relaxed` callers (email) may opt out with FLOWPILOT_ALLOW_PRIVATE_HOSTS=1."""
    if relaxed and os.getenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS") == "1": return
    if hostname in {"localhost", "metadata.google.internal"} or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Local/private addresses are blocked")
    try:
        answers = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(item[4][0]).is_global for item in answers):
            raise ValueError("Only public IP addresses are allowed")
    except socket.gaierror as exc: raise ValueError("Cannot resolve destination host") from exc


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


# ---------------------------------------------------------------------------
# Credentials vault: Fernet-encrypted at rest, decrypted only while a run is in progress
# ---------------------------------------------------------------------------
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
MAX_CREDENTIALS = 100
_fernets: dict[str, Fernet] = {}
_secrets_var: contextvars.ContextVar[dict] = contextvars.ContextVar("fp_secrets", default={})
_stack_var: contextvars.ContextVar[tuple] = contextvars.ContextVar("fp_call_stack", default=())


def fernet() -> Fernet:
    path = Path(os.getenv("FLOWPILOT_KEY_FILE", str(DB_PATH.parent / "flowpilot.key")))
    cached = _fernets.get(str(path))
    if cached is None:
        if path.exists():
            key = path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(key)
            try: os.chmod(path, 0o600)
            except OSError: pass  # best effort on Windows
        cached = _fernets[str(path)] = Fernet(key)
    return cached


def load_secrets() -> dict[str, str | None]:
    with conn() as db:
        rows = db.execute("SELECT name, value_enc FROM credentials").fetchall()
    if not rows: return {}
    f, out = fernet(), {}
    for r in rows:
        try: out[r["name"]] = f.decrypt(r["value_enc"].encode()).decode()
        except InvalidToken: out[r["name"]] = None  # key file was replaced; reported when used
    return out


def lookup_secret(name: str) -> str:
    secrets_ = _secrets_var.get()
    if name not in secrets_: raise ValueError(f"Unknown credential: {name or '(none chosen)'}")
    value = secrets_[name]
    if value is None: raise ValueError(f"Credential {name} cannot be decrypted. Was the key file replaced?")
    return value


def oauth_config(raw: str | None) -> dict | None:
    if not raw or not raw.startswith('{"oauth2"'): return None
    try: cfg = json.loads(raw)
    except ValueError: return None
    return cfg if isinstance(cfg, dict) and cfg.get("oauth2") == 1 else None


def redact(result: dict, secrets_: dict) -> dict:
    """Hide every credential value anywhere in a run result (outputs, inputs, errors)."""
    found: set[str] = set()
    for v in secrets_.values():
        if not v: continue
        found.add(v)
        cfg = oauth_config(v)
        if cfg: found.update(str(cfg.get(k)) for k in ("access_token", "refresh_token", "client_secret") if cfg.get(k))
    values = sorted({v for v in found if len(v) >= 4}, key=len, reverse=True)
    if not values: return result
    text = json.dumps(result, default=str)
    for v in values:
        for variant in {v, json.dumps(v)[1:-1]}:
            text = text.replace(variant, "[hidden]")
    return json.loads(text)


class CredentialIn(BaseModel):
    value: str = Field(min_length=4, max_length=4000)


@app.get("/api/credentials")
def list_credentials():
    """Names only. Values are never returned by any endpoint."""
    with conn() as db:
        rows = db.execute("SELECT name, value_enc, created_at, updated_at FROM credentials ORDER BY name").fetchall()
    out = []
    for r in rows:
        try: cfg = oauth_config(fernet().decrypt(r["value_enc"].encode()).decode())
        except InvalidToken: cfg = None
        out.append({"name": r["name"], "created_at": r["created_at"], "updated_at": r["updated_at"], "kind": "oauth2" if cfg else "secret", "provider": cfg.get("provider") if cfg else None})
    return out


@app.put("/api/credentials/{name}")
def save_credential(name: str, body: CredentialIn):
    if not NAME_RE.fullmatch(name): raise HTTPException(400, "Name must start with a letter and use only letters, numbers, _ and - (max 40)")
    if "\r" in body.value or "\n" in body.value: raise HTTPException(400, "Credential values cannot contain line breaks")
    encrypted = fernet().encrypt(body.value.encode()).decode()
    with conn() as db:
        exists = db.execute("SELECT 1 FROM credentials WHERE name=?", (name,)).fetchone()
        if not exists and db.execute("SELECT COUNT(*) AS n FROM credentials").fetchone()["n"] >= MAX_CREDENTIALS:
            raise HTTPException(400, f"At most {MAX_CREDENTIALS} credentials are allowed")
        ts = now()
        if exists: db.execute("UPDATE credentials SET value_enc=?, updated_at=? WHERE name=?", (encrypted, ts, name))
        else: db.execute("INSERT INTO credentials (name,value_enc,created_at,updated_at) VALUES (?,?,?,?)", (name, encrypted, ts, ts))
    return {"name": name, "saved": True}


@app.delete("/api/credentials/{name}", status_code=204)
def delete_credential(name: str):
    with conn() as db:
        db.execute("DELETE FROM credentials WHERE name=?", (name,))


# ---------------------------------------------------------------------------
# Expressions inside {{ }}: {{ price * qty }}, {{ name.upper() }}, {{ "big" if total > 100 else "small" }}
# They are evaluated by walking a parsed syntax tree and allowing only a small safe set of operations. Nothing is
# ever executed as code, there is no attribute access beyond reading data, and there are size and step limits.
# ---------------------------------------------------------------------------
PLAIN_PATH_RE = re.compile(r"^[A-Za-z0-9_.\-]*$")
EXPR_MAX_NODES = 200
EXPR_MAX_ITEMS = 1000
EXPR_MAX_TEXT = 200_000


class ExprError(ValueError):
    pass


def _expr_now() -> str: return datetime.now(timezone.utc).isoformat()
def _expr_today() -> str: return datetime.now(timezone.utc).date().isoformat()


EXPR_FUNCS = {"len": len, "str": str, "int": int, "float": float, "round": round, "min": min, "max": max, "sum": sum, "abs": abs, "sorted": sorted,
              "bool": bool, "list": list, "any": any, "all": all, "now": _expr_now, "today": _expr_today}
EXPR_METHODS = {"upper", "lower", "strip", "lstrip", "rstrip", "title", "capitalize", "replace", "split", "join", "startswith", "endswith", "find",
                "count", "zfill", "get", "keys", "values", "items", "index", "isdigit", "isalpha"}
EXPR_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
EXPR_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
            ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b, ast.Is: operator.is_, ast.IsNot: operator.is_not}


def eval_expression(expr: str, data: Any) -> Any:
    if len(expr) > 1000: raise ExprError("the expression is too long")
    try: tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc: raise ExprError(f"syntax error ({exc.msg})")
    except (RecursionError, ValueError, MemoryError): raise ExprError("the expression cannot be read")
    count = [0]

    def ev(n: ast.AST) -> Any:
        count[0] += 1
        if count[0] > EXPR_MAX_NODES: raise ExprError("the expression is too complex")
        if isinstance(n, ast.Constant):
            if isinstance(n.value, (str, int, float, bool)) or n.value is None: return n.value
            raise ExprError("unsupported value")
        if isinstance(n, ast.Name):
            if n.id == "data": return data
            if n.id in ("cred", "__builtins__") or n.id.startswith("_"): raise ExprError(f"'{n.id}' is not available in expressions")
            return data.get(n.id) if isinstance(data, dict) else None
        if isinstance(n, ast.Attribute):
            if n.attr.startswith("_"): raise ExprError("names starting with _ are not allowed")
            base = ev(n.value)
            return base.get(n.attr) if isinstance(base, dict) else None
        if isinstance(n, ast.Subscript):
            base = ev(n.value)
            if isinstance(n.slice, ast.Slice):
                lo, hi = (ev(x) if x is not None else None for x in (n.slice.lower, n.slice.upper))
                if n.slice.step is not None: raise ExprError("slice steps are not supported")
                if not isinstance(base, (str, list)) or not all(v is None or isinstance(v, int) for v in (lo, hi)): raise ExprError("invalid slice")
                return base[lo:hi]
            key = ev(n.slice)
            try:
                if isinstance(base, dict): return base.get(key)
                if isinstance(base, (list, str)) and isinstance(key, int) and not isinstance(key, bool): return base[key] if -len(base) <= key < len(base) else None
            except TypeError: pass
            return None
        if isinstance(n, ast.BinOp):
            op = EXPR_BIN.get(type(n.op))
            if op is None: raise ExprError("unsupported operator")
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and (not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or abs(b) > 64 or abs(a) > 1e6): raise ExprError("that power is too large")
            if isinstance(n.op, ast.Mult) and isinstance(a, (str, list)) and isinstance(b, int) and len(a) * max(b, 0) > EXPR_MAX_TEXT: raise ExprError("the result is too large")
            if isinstance(n.op, ast.Mult) and isinstance(b, (str, list)) and isinstance(a, int) and len(b) * max(a, 0) > EXPR_MAX_TEXT: raise ExprError("the result is too large")
            if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
                if not (isinstance(n.op, ast.Add) and isinstance(a, str) and isinstance(b, str)): raise ExprError("cannot calculate with true, false or empty values")
            try: return op(a, b)
            except ZeroDivisionError: raise ExprError("division by zero")
            except TypeError: raise ExprError("these values cannot be combined that way")
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if isinstance(n.op, ast.Not): return not v
            if isinstance(n.op, (ast.USub, ast.UAdd)) and isinstance(v, (int, float)) and not isinstance(v, bool): return -v if isinstance(n.op, ast.USub) else v
            raise ExprError("unsupported operator")
        if isinstance(n, ast.BoolOp):
            result = None
            for v in n.values:
                result = ev(v)
                if isinstance(n.op, ast.And) and not result: return result
                if isinstance(n.op, ast.Or) and result: return result
            return result
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                right = ev(comp)
                fn = EXPR_CMP.get(type(op))
                if fn is None: raise ExprError("unsupported comparison")
                try: ok = fn(left, right)
                except TypeError: raise ExprError("these values cannot be compared")
                if not ok: return False
                left = right
            return True
        if isinstance(n, ast.IfExp): return ev(n.body) if ev(n.test) else ev(n.orelse)
        if isinstance(n, (ast.List, ast.Tuple)):
            if len(n.elts) > EXPR_MAX_ITEMS: raise ExprError("too many items")
            return [ev(x) for x in n.elts]
        if isinstance(n, ast.Dict):
            if len(n.keys) > EXPR_MAX_ITEMS or any(k is None for k in n.keys): raise ExprError("unsupported dictionary")
            return {ev(k): ev(v) for k, v in zip(n.keys, n.values)}
        if isinstance(n, ast.Call):
            if n.keywords or any(isinstance(a, ast.Starred) for a in n.args): raise ExprError("keyword and starred arguments are not supported")
            args = [ev(a) for a in n.args]
            if isinstance(n.func, ast.Name):
                fn = EXPR_FUNCS.get(n.func.id)
                if fn is None: raise ExprError(f"unknown function {n.func.id}()")
                try: out = fn(*args)
                except (TypeError, ValueError) as exc: raise ExprError(f"{n.func.id}() could not be applied: {str(exc)[:80]}")
                if isinstance(out, (str, list)) and len(out) > EXPR_MAX_TEXT: raise ExprError("the result is too large")
                return out
            if isinstance(n.func, ast.Attribute):
                name = n.func.attr
                if name not in EXPR_METHODS: raise ExprError(f"unknown method .{name}()")
                recv = ev(n.func.value)
                if not isinstance(recv, (str, list, dict)): raise ExprError(f".{name}() cannot be used on this value")
                if isinstance(recv, dict) and name not in ("get", "keys", "values", "items"): raise ExprError(f".{name}() cannot be used on an object")
                if isinstance(recv, list) and name not in ("count", "index", "join"): raise ExprError(f".{name}() cannot be used on a list")
                if isinstance(recv, str) and name in ("get", "keys", "values", "items"): raise ExprError(f".{name}() cannot be used on text")
                try:
                    if name == "join":
                        sep = recv if isinstance(recv, str) else ","
                        items = args[0] if args else []
                        if not isinstance(items, list): raise ExprError("join() needs a list")
                        return sep.join(str(x) for x in items)
                    out = getattr(recv, name)(*args)
                except (TypeError, ValueError, KeyError) as exc: raise ExprError(f".{name}() could not be applied: {str(exc)[:80]}")
                if name in ("keys", "values", "items"): out = [list(x) if isinstance(x, tuple) else x for x in out]
                if isinstance(out, (str, list)) and len(out) > EXPR_MAX_TEXT: raise ExprError("the result is too large")
                return out
            raise ExprError("unsupported call")
        raise ExprError(f"{type(n).__name__} is not allowed in expressions")

    try: return ev(tree.body)
    except RecursionError: raise ExprError("the expression is nested too deeply")


def render_template(text: str, data: Any, json_escape: bool = False) -> str:
    """Replace {{path}} with values from the incoming data and {{cred.NAME}} with a stored credential."""
    def repl(match):
        path = match.group(1).strip()
        if path.startswith("cred."):
            value = lookup_secret(path[5:])
            if oauth_config(value): raise ValueError("An OAuth connection cannot be inserted as text. Choose it in the node's Authentication or Connection setting.")
        elif PLAIN_PATH_RE.fullmatch(path): value = get_path(data, path)
        else:
            try: value = eval_expression(path, data)
            except ExprError as exc: raise ValueError(f"Expression error in {{{{ {path[:60]} }}}}: {exc}")
        if value is None: return ""
        if isinstance(value, str): return json.dumps(value)[1:-1] if json_escape else value
        return json.dumps(value)
    return re.sub(r"\{\{(.*?)\}\}", repl, text)


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
BLOCKED_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "upgrade", "te", "trailer", "proxy-authorization", "proxy-connection"}
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def parse_headers(text: str) -> dict[str, str]:
    """Parse the HTTP node's headers field: a JSON object of header names to values."""
    if not text.strip(): return {}
    raw = read_json(text)
    if not isinstance(raw, dict): raise ValueError('Headers must be a JSON object, for example {"Authorization": "Bearer token"}')
    if len(raw) > 20: raise ValueError("At most 20 headers are allowed")
    out = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not HEADER_NAME_RE.fullmatch(name): raise ValueError(f"Invalid header name: {name!r}")
        if name.lower() in BLOCKED_HEADERS: raise ValueError(f"Header not allowed: {name}")
        if isinstance(value, (dict, list)) or value is None: raise ValueError(f"Header {name} must be text or a number")
        text_value = str(value)
        if len(text_value) > 2000 or "\r" in text_value or "\n" in text_value: raise ValueError(f"Invalid value for header {name}")
        out[name] = text_value
    return out


def apply_auth(headers: dict, d: dict) -> None:
    """Add an Authorization (or custom) header from a stored credential, when the node asks for one."""
    kind = str(d.get("auth_type") or "none")
    if kind == "none": return
    if kind == "oauth2":
        headers["Authorization"] = f"Bearer {oauth_access_token(str(d.get('auth_credential') or '').strip())}"
        return
    secret = lookup_secret(str(d.get("auth_credential") or "").strip())
    if kind == "bearer": headers["Authorization"] = f"Bearer {secret}"
    elif kind == "basic": headers["Authorization"] = "Basic " + base64.b64encode(secret.encode()).decode()
    elif kind == "header":
        name = str(d.get("auth_header") or "").strip()
        if not HEADER_NAME_RE.fullmatch(name) or name.lower() in BLOCKED_HEADERS: raise ValueError(f"Invalid authentication header name: {name!r}")
        headers[name] = secret
    else: raise ValueError(f"Unknown authentication type: {kind}")


def do_request(method: str, url: str, headers: dict, **kwargs) -> Any:
    # Never follow redirects, so a public URL cannot bounce to an internal address.
    timeout = kwargs.pop("timeout", 8.0)
    raw = kwargs.pop("raw", False)
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        response = client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        if raw:
            if len(response.content) > FILE_MAX: raise ValueError("The download exceeds 5 MB")
            return {"bytes": response.content, "content_type": str(getattr(response, "headers", {}).get("content-type", ""))}
        if len(response.content) > 1_000_000: raise ValueError("HTTP response exceeds 1 MB")
        try: return response.json()
        except ValueError: return response.text


EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def send_email(d: dict, previous: Any) -> dict:
    cred = urlparse(lookup_secret(str(d.get("credential") or "").strip()))
    if cred.scheme not in ("smtp", "smtps") or not cred.hostname:
        raise ValueError("The SMTP credential must look like smtp://user:password@host:587?from=you@example.com (URL-encode special characters)")
    query = parse_qs(cred.query)
    sender = (query.get("from", [unquote(cred.username or "")])[0]).strip()
    security = query.get("security", ["ssl" if cred.scheme == "smtps" else "starttls"])[0].lower()
    if security not in ("ssl", "starttls", "none"): raise ValueError("security must be ssl, starttls or none")
    port = cred.port or {"ssl": 465, "starttls": 587, "none": 25}[security]
    if not EMAIL_RE.fullmatch(sender): raise ValueError("The SMTP credential needs a valid from= address")
    assert_public_host(cred.hostname.lower(), relaxed=True)
    recipients = [x.strip() for x in render_template(str(d.get("to", "")), previous).split(",") if x.strip()]
    if not 1 <= len(recipients) <= 10: raise ValueError("Provide between 1 and 10 recipients")
    for r in recipients:
        if not EMAIL_RE.fullmatch(r): raise ValueError(f"Invalid recipient address: {r!r}")
    subject = render_template(str(d.get("subject", "")), previous)
    if "\r" in subject or "\n" in subject or len(subject) > 200: raise ValueError("Subject must be one line of at most 200 characters")
    body = render_template(str(d.get("body", "")), previous)
    if len(body) > 100_000: raise ValueError("Email body is too large")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(recipients), subject
    msg.set_content(body)
    smtp_cls = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
    with smtp_cls(cred.hostname, port, timeout=10) as smtp:
        if security == "starttls": smtp.starttls(context=ssl.create_default_context())
        if cred.username: smtp.login(unquote(cred.username), unquote(cred.password or ""))
        smtp.send_message(msg)
    return {"sent_to": recipients, "subject": subject}


def send_notify(d: dict, previous: Any) -> dict:
    service = str(d.get("service") or "slack").lower()
    if service not in ("slack", "discord", "generic"): raise ValueError(f"Unknown service: {service}")
    url = safe_url(render_template(str(d.get("url", "")), previous))
    message = render_template(str(d.get("message", "")), previous)
    if not message.strip(): raise ValueError("The message is empty")
    if len(message) > 4000: raise ValueError("The message is longer than 4000 characters")
    payload = {"content": message} if service == "discord" else {"text": message}
    do_request("POST", url, {}, json=payload)
    return {"sent": True, "service": service}


def parse_csv_text(d: dict, previous: Any) -> list:
    path = str(d.get("path") or "").strip()
    text = get_path(previous, path) if path else previous
    if not isinstance(text, str): raise ValueError("Parse CSV needs text as input. Use 'Text field' to point at it.")
    if len(text) > 1_000_000: raise ValueError("CSV text exceeds 1 MB")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) > 10_001: raise ValueError("CSV has more than 10000 rows")
    if str(d.get("header", "true")).lower() == "false": return rows
    if not rows: return []
    keys = [k.strip() for k in rows[0]]
    return [dict(zip(keys, row)) for row in rows[1:]]


PINNABLE = {"input", "transform", "template", "http", "delay", "set", "filter", "csv", "notify", "email", "subflow", "code", "sql", "rss", "sheets", "ai", "file"}
# Nodes that support retries and "continue on error".
ERROR_SETTINGS = {"input", "transform", "template", "set", "filter", "csv", "http", "notify", "email", "subflow", "code", "delay", "sql", "rss", "telegram", "github", "sheets", "gmail", "ai", "file"}
MAX_RETRIES = 5
MAX_RETRY_WAIT = 3.0
CODE_TIMEOUT = 5
CODE_OUTPUT_LIMIT = 1_000_000
MAX_STEPS = 500
MAX_LOOP_ITEMS = 100
MAX_SUBFLOW_DEPTH = 3
PREVIEW_LIMIT = 20_000


# ---------------------------------------------------------------------------
# Code node: a short Python snippet run in a separate, locked-down process with a time limit.
# It is a convenience for the trusted owner, not a security boundary against hostile code.
# ---------------------------------------------------------------------------
CODE_RUNNER = r"""
import sys, json, ast, re, math, datetime
from types import SimpleNamespace as NS
try:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (768 * 2**20, 768 * 2**20))
except Exception:
    pass
payload = json.loads(sys.stdin.read())
def fail(msg):
    sys.stdout.write(json.dumps({"error": msg}))
    sys.exit(0)
try:
    tree = ast.parse(payload["code"], "code", "exec")
except SyntaxError as exc:
    fail("Syntax error on line %s: %s" % (exc.lineno, exc.msg))
BLOCKED = {"format", "format_map", "mro"}
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        fail("import is not available. json, re, math and datetime are already provided.")
    if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in BLOCKED or node.attr.startswith(("gi_", "cr_", "ag_", "tb_", "f_"))):
        fail("Access to '%s' is not allowed" % node.attr)
    if isinstance(node, ast.Name) and node.id.startswith("__"):
        fail("Access to '%s' is not allowed" % node.id)
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        fail("global and nonlocal are not allowed")
names = "abs all any bool dict enumerate filter float int len list map max min range reversed round set sorted str sum tuple zip isinstance ValueError TypeError KeyError Exception".split()
import builtins
safe = {n: getattr(builtins, n) for n in names}
env = {
    "__builtins__": safe,
    "input": payload["input"],
    "result": None,
    "json": NS(loads=json.loads, dumps=json.dumps),
    "re": NS(search=re.search, match=re.match, fullmatch=re.fullmatch, findall=re.findall, sub=re.sub, split=re.split, compile=re.compile, escape=re.escape, IGNORECASE=re.I, MULTILINE=re.M),
    "math": NS(**{k: getattr(math, k) for k in dir(math) if not k.startswith("_")}),
    "datetime": NS(datetime=datetime.datetime, date=datetime.date, timedelta=datetime.timedelta, timezone=datetime.timezone),
}
try:
    exec(compile(tree, "code", "exec"), env)
    out = json.dumps({"result": env.get("result")}, default=str)
except BaseException as exc:
    fail("%s: %s" % (type(exc).__name__, exc))
sys.stdout.write(out)
"""


def run_code(d: dict, previous: Any) -> Any:
    code = str(d.get("code") or "")
    if not code.strip(): raise ValueError("Write some code first. Read the data from 'input' and put the answer in 'result'.")
    if len(code) > 20_000: raise ValueError("Code is limited to 20000 characters")
    try: payload = json.dumps({"code": code, "input": previous}, default=str)
    except (TypeError, ValueError) as exc: raise ValueError(f"Input cannot be passed to code: {exc}")
    if len(payload) > 1_000_000: raise ValueError("Input is too large for the Code node (limit 1 MB)")
    env = {"PYTHONIOENCODING": "utf-8", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    try:
        with tempfile.TemporaryDirectory() as cwd:
            proc = subprocess.run([sys.executable, "-I", "-c", CODE_RUNNER], input=payload, capture_output=True, text=True,
                                  encoding="utf-8", timeout=CODE_TIMEOUT, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        raise ValueError(f"Code took longer than {CODE_TIMEOUT} seconds and was stopped")
    out = proc.stdout or ""
    if len(out) > CODE_OUTPUT_LIMIT: raise ValueError("Code produced too much output (limit 1 MB)")
    try: res = json.loads(out)
    except ValueError: raise ValueError("Code stopped unexpectedly" + (" (out of memory?)" if proc.returncode else ""))
    if "error" in res: raise ValueError(res["error"])
    return res.get("result")


# ---------------------------------------------------------------------------
# Database, feed and chat/code-host nodes
# ---------------------------------------------------------------------------
SQL_ROW_LIMIT = 1000
MAX_WAIT_SECONDS = 60.0
MAX_LONG_WAIT_SECONDS = 7 * 86400
MAX_PENDING_WAITS = 200
WAIT_UNITS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}


def wait_seconds(d: dict) -> float:
    try: amount = float(d.get("seconds", 1))
    except (TypeError, ValueError): raise ValueError("Wait time must be a number")
    unit = str(d.get("unit") or "seconds")
    if unit not in WAIT_UNITS: raise ValueError("Wait unit must be seconds, minutes, hours or days")
    total = max(0.0, amount * WAIT_UNITS[unit])
    if total > MAX_LONG_WAIT_SECONDS: raise ValueError("A Wait is limited to 7 days")
    return total
_response_var: contextvars.ContextVar[dict] = contextvars.ContextVar("fp_response", default={})


def jsonable(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)): return bytes(value).hex()
    if isinstance(value, (str, int, float, bool)) or value is None: return value
    if isinstance(value, (list, tuple)): return [jsonable(v) for v in value]
    if isinstance(value, dict): return {str(k): jsonable(v) for k, v in value.items()}
    return str(value)


def sqlite_folder() -> Path:
    return Path(os.getenv("FLOWPILOT_SQLITE_DIR", str(BASE / "data"))).resolve()


def run_sql(d: dict, previous: Any) -> Any:
    cred = urlparse(lookup_secret(str(d.get("credential") or "").strip()))
    query = str(d.get("query") or "").strip()
    if not query: raise ValueError("Write a SQL query")
    if len(query) > 10_000: raise ValueError("The query is limited to 10000 characters")
    raw = str(d.get("params") or "").strip()
    params: Any = []
    if raw:
        try: params = json.loads(render_template(raw, previous, json_escape=True))
        except ValueError: raise ValueError("Parameters must be a JSON list such as [\"{{name}}\", 5]")
        if not isinstance(params, list) or len(params) > 100: raise ValueError("Parameters must be a JSON list of at most 100 values")
    if cred.scheme == "sqlite":
        rel = unquote(cred.path).lstrip("/")
        if not rel or ".." in rel.replace("\\", "/").split("/") or re.match(r"^[A-Za-z]:", rel): raise ValueError("The SQLite credential must look like sqlite:///shop.db (a file inside the data folder)")
        folder = sqlite_folder()
        target = (folder / rel).resolve()
        if folder not in target.parents: raise ValueError("SQLite files must live inside the data folder")
        folder.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(target, timeout=5)
        deny = {getattr(sqlite3, "SQLITE_ATTACH", 24), getattr(sqlite3, "SQLITE_DETACH", 25)}
        db.set_authorizer(lambda action, *_a: sqlite3.SQLITE_DENY if action in deny else sqlite3.SQLITE_OK)
        db.row_factory = sqlite3.Row
        try:
            cur = db.execute(query, params)
            if cur.description is None:
                db.commit()
                return {"rows_affected": cur.rowcount}
            rows = cur.fetchmany(SQL_ROW_LIMIT + 1)
            if len(rows) > SQL_ROW_LIMIT: raise ValueError(f"The query returned more than {SQL_ROW_LIMIT} rows. Add a LIMIT.")
            return [jsonable(dict(r)) for r in rows]
        except sqlite3.Error as exc: raise ValueError(f"SQLite error: {exc}")
        finally: db.close()
    if cred.scheme in ("postgres", "postgresql"):
        try: import psycopg
        except ImportError: raise ValueError("Postgres support needs the psycopg package: pip install \"psycopg[binary]\"")
        from psycopg.rows import dict_row
        if not cred.hostname: raise ValueError("The Postgres credential must look like postgresql://user:password@host:5432/database")
        assert_public_host(cred.hostname.lower(), relaxed=True)
        sslmode = parse_qs(cred.query).get("sslmode", ["prefer"])[0]
        try:
            with psycopg.connect(host=cred.hostname, port=cred.port or 5432, user=unquote(cred.username or ""), password=unquote(cred.password or ""),
                                 dbname=unquote(cred.path.lstrip("/")), connect_timeout=8, sslmode=sslmode, options="-c statement_timeout=8000", row_factory=dict_row) as conn_pg:
                cur = conn_pg.execute(query, params)
                if cur.description is None: return {"rows_affected": cur.rowcount}
                rows = cur.fetchmany(SQL_ROW_LIMIT + 1)
                if len(rows) > SQL_ROW_LIMIT: raise ValueError(f"The query returned more than {SQL_ROW_LIMIT} rows. Add a LIMIT.")
                return [jsonable(r) for r in rows]
        except psycopg.Error as exc: raise ValueError(f"Postgres error: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
    raise ValueError("The database credential must start with sqlite:/// or postgresql://")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_rss(d: dict, previous: Any) -> list:
    url = safe_url(render_template(str(d.get("url", "")), previous))
    try: limit = max(1, min(int(float(d.get("limit") or 10)), 50))
    except (TypeError, ValueError): raise ValueError("Limit must be a number")
    text = do_request("GET", url, {"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
    if not isinstance(text, str): raise ValueError("The URL did not return an RSS or Atom feed")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper(): raise ValueError("Feeds with DOCTYPE or entity declarations are not supported")
    try: root = ET.fromstring(text)
    except ET.ParseError: raise ValueError("The feed is not valid XML")
    out = []
    for item in root.iter():
        if _local(item.tag) not in ("item", "entry"): continue
        row: dict[str, str] = {}
        for child in item:
            name = _local(child.tag)
            if name == "link":
                row.setdefault("link", child.get("href") or (child.text or "").strip())
            elif name == "title": row["title"] = (child.text or "").strip()
            elif name in ("pubDate", "published", "updated"): row.setdefault("published", (child.text or "").strip())
            elif name in ("description", "summary", "content"): row.setdefault("summary", re.sub(r"<[^>]+>", "", child.text or "").strip()[:500])
        out.append({"title": row.get("title", ""), "link": row.get("link", ""), "published": row.get("published", ""), "summary": row.get("summary", "")})
        if len(out) >= limit: break
    return out


def send_telegram(d: dict, previous: Any) -> dict:
    token = lookup_secret(str(d.get("credential") or "").strip())
    if not re.fullmatch(r"\d{3,15}:[A-Za-z0-9_-]{20,80}", token): raise ValueError("The Telegram credential must be the bot token from BotFather")
    chat = render_template(str(d.get("chat_id", "")), previous).strip()
    if not re.fullmatch(r"-?\d{1,20}|@[A-Za-z0-9_]{4,64}", chat): raise ValueError("Chat ID must be a number or @channelname")
    text = render_template(str(d.get("text", "")), previous)
    if not text.strip() or len(text) > 4000: raise ValueError("The message must be between 1 and 4000 characters")
    do_request("POST", f"https://api.telegram.org/bot{token}/sendMessage", {}, json={"chat_id": chat, "text": text})
    return {"sent": True, "chat_id": chat}


GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def github_action(d: dict, previous: Any) -> Any:
    token = lookup_secret(str(d.get("credential") or "").strip())
    repo = render_template(str(d.get("repo", "")), previous).strip()
    if not GITHUB_REPO_RE.fullmatch(repo): raise ValueError("Repository must look like owner/name")
    op = str(d.get("operation") or "create_issue")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "flowpilot"}
    base = f"https://api.github.com/repos/{repo}/issues"
    if op == "create_issue":
        title = render_template(str(d.get("title", "")), previous).strip()
        if not title or len(title) > 250: raise ValueError("The issue title must be between 1 and 250 characters")
        body = render_template(str(d.get("body", "")), previous)
        if len(body) > 60_000: raise ValueError("The issue body is too large")
        res = do_request("POST", base, headers, json={"title": title, "body": body})
        return {"number": res.get("number"), "url": res.get("html_url")} if isinstance(res, dict) else res
    if op == "list_issues":
        state = str(d.get("state") or "open")
        if state not in ("open", "closed", "all"): raise ValueError("State must be open, closed or all")
        res = do_request("GET", f"{base}?state={state}&per_page=30", headers)
        if not isinstance(res, list): raise ValueError("Unexpected response from GitHub")
        return [{"number": i.get("number"), "title": i.get("title"), "state": i.get("state"), "url": i.get("html_url"), "user": (i.get("user") or {}).get("login")} for i in res if "pull_request" not in i]
    raise ValueError(f"Unknown GitHub operation: {op}")


def build_response(d: dict, previous: Any) -> dict:
    try: status = int(float(d.get("status") or 200))
    except (TypeError, ValueError): raise ValueError("Status must be a number")
    if not 200 <= status <= 599: raise ValueError("Status must be between 200 and 599")
    raw = str(d.get("body") if d.get("body") is not None else "")
    try: body = json.loads(render_template(raw, previous, json_escape=True)) if raw.strip() else None
    except ValueError: body = render_template(raw, previous)
    if len(json.dumps(body, default=str)) > 256 * 1024: raise ValueError("The response body is too large")
    return {"status": status, "body": body}


# ---------------------------------------------------------------------------
# OAuth 2.0 connections (authorization code + PKCE). You bring your own app (client id and secret), so nothing is
# shared with anyone else. Tokens are stored encrypted in the credentials vault and refreshed automatically.
# ---------------------------------------------------------------------------
OAUTH_PROVIDERS = {
    "google": {"auth_url": "https://accounts.google.com/o/oauth2/v2/auth", "token_url": "https://oauth2.googleapis.com/token",
               "extra": {"access_type": "offline", "prompt": "consent"},
               "scopes": "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/gmail.send"},
    "github": {"auth_url": "https://github.com/login/oauth/authorize", "token_url": "https://github.com/login/oauth/access_token", "extra": {}, "scopes": "repo"},
}
OAUTH_STATE_SECONDS = 600
SCOPE_RE = re.compile(r"^[A-Za-z0-9_:/.\- ]{1,500}$")


def public_url() -> str:
    return os.getenv("FLOWPILOT_PUBLIC_URL", "http://127.0.0.1:5173").rstrip("/")


def store_credential(name: str, value: str) -> None:
    encrypted = fernet().encrypt(value.encode()).decode()
    with conn() as db:
        ts = now()
        if db.execute("SELECT 1 FROM credentials WHERE name=?", (name,)).fetchone():
            db.execute("UPDATE credentials SET value_enc=?, updated_at=? WHERE name=?", (encrypted, ts, name))
        else:
            if db.execute("SELECT COUNT(*) n FROM credentials").fetchone()["n"] >= MAX_CREDENTIALS: raise ValueError(f"At most {MAX_CREDENTIALS} credentials are allowed")
            db.execute("INSERT INTO credentials (name,value_enc,created_at,updated_at) VALUES (?,?,?,?)", (name, encrypted, ts, ts))


def token_request(token_url: str, data: dict) -> dict:
    safe_url(token_url)
    res = do_request("POST", token_url, {"Accept": "application/json"}, data=data)
    if not isinstance(res, dict) or not res.get("access_token"):
        detail = res.get("error_description") or res.get("error") if isinstance(res, dict) else None
        raise ValueError(f"The provider did not return an access token{': ' + str(detail)[:150] if detail else ''}")
    return res


def oauth_access_token(name: str) -> str:
    """A valid access token for an OAuth connection, refreshed (and saved) when it is about to expire."""
    cfg = oauth_config(lookup_secret(name))
    if not cfg: raise ValueError(f"{name or '(none chosen)'} is not an OAuth connection. Connect an account on the Credentials page.")
    if float(cfg.get("expires_at") or 0) - time.time() > 60 and cfg.get("access_token"): return cfg["access_token"]
    if not cfg.get("refresh_token"): raise ValueError(f"The connection {name} has expired and cannot be refreshed. Reconnect the account.")
    res = token_request(cfg["token_url"], {"grant_type": "refresh_token", "refresh_token": cfg["refresh_token"], "client_id": cfg["client_id"], "client_secret": cfg["client_secret"]})
    cfg["access_token"] = res["access_token"]
    cfg["expires_at"] = time.time() + float(res.get("expires_in") or 3600)
    if res.get("refresh_token"): cfg["refresh_token"] = res["refresh_token"]
    raw = json.dumps(cfg)
    store_credential(name, raw)
    _secrets_var.get()[name] = raw  # later steps in this run, and the redaction of this run, see the new token
    return cfg["access_token"]


class OAuthStartIn(BaseModel):
    name: str
    provider: str
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: str = Field(min_length=1, max_length=500)
    scopes: str = ""
    auth_url: str = ""
    token_url: str = ""


@app.get("/api/oauth/info")
def oauth_info():
    return {"redirect_uri": public_url() + "/api/oauth/callback", "providers": {k: {"scopes": v["scopes"]} for k, v in OAUTH_PROVIDERS.items()}}


@app.post("/api/oauth/start")
def oauth_start(body: OAuthStartIn):
    if not NAME_RE.fullmatch(body.name): raise HTTPException(400, "Name must start with a letter and use only letters, numbers, _ and - (max 40)")
    if any(c.isspace() for c in body.client_id + body.client_secret): raise HTTPException(400, "Client ID and secret cannot contain spaces or line breaks")
    preset = OAUTH_PROVIDERS.get(body.provider)
    if preset is None and body.provider != "custom": raise HTTPException(400, "Provider must be google, github or custom")
    auth_url = preset["auth_url"] if preset else body.auth_url.strip()
    token_url = preset["token_url"] if preset else body.token_url.strip()
    for u in (auth_url, token_url):
        parsed = urlparse(u)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password: raise HTTPException(400, "Authorization and token URLs must be HTTPS URLs")
    scopes = (body.scopes or (preset or {}).get("scopes", "")).strip()
    if scopes and not SCOPE_RE.fullmatch(scopes): raise HTTPException(400, "Scopes contain unsupported characters")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    redirect_uri = public_url() + "/api/oauth/callback"
    payload = {"name": body.name, "provider": body.provider, "client_id": body.client_id, "client_secret": body.client_secret, "scopes": scopes, "token_url": token_url, "verifier": verifier, "redirect_uri": redirect_uri}
    with conn() as db:
        db.execute("DELETE FROM oauth_pending WHERE created_at < ?", (time.time() - OAUTH_STATE_SECONDS,))
        db.execute("INSERT INTO oauth_pending (state,payload_enc,created_at) VALUES (?,?,?)", (state, fernet().encrypt(json.dumps(payload).encode()).decode(), time.time()))
    params = {"response_type": "code", "client_id": body.client_id, "redirect_uri": redirect_uri, "state": state, "code_challenge": challenge, "code_challenge_method": "S256", **(preset or {}).get("extra", {})}
    if scopes: params["scope"] = scopes
    return {"auth_url": auth_url + ("&" if "?" in auth_url else "?") + urlencode(params), "redirect_uri": redirect_uri}


@app.get("/api/oauth/callback")
def oauth_callback(code: str = "", state: str = "", error: str = ""):
    """Where the provider sends the browser back. The one-time secret state (not a cookie) proves who started the flow."""
    def back(kind: str, msg: str = "") -> RedirectResponse:
        return RedirectResponse(f"/?oauth={kind}" + (f"&reason={quote(msg[:160])}" if msg else ""), status_code=303)
    with conn() as db:
        row = db.execute("SELECT payload_enc, created_at FROM oauth_pending WHERE state=?", (state,)).fetchone()
        if row: db.execute("DELETE FROM oauth_pending WHERE state=?", (state,))  # single use
    if not row or time.time() - row["created_at"] > OAUTH_STATE_SECONDS: return back("error", "This connection request expired or was not started here. Try again.")
    if error or not code: return back("error", f"The provider refused the request: {error or 'no code returned'}")
    try:
        payload = json.loads(fernet().decrypt(row["payload_enc"].encode()).decode())
        res = token_request(payload["token_url"], {"grant_type": "authorization_code", "code": code, "redirect_uri": payload["redirect_uri"],
                                                   "client_id": payload["client_id"], "client_secret": payload["client_secret"], "code_verifier": payload["verifier"]})
        cfg = {"oauth2": 1, "provider": payload["provider"], "client_id": payload["client_id"], "client_secret": payload["client_secret"], "token_url": payload["token_url"],
               "scopes": payload["scopes"], "access_token": res["access_token"], "refresh_token": res.get("refresh_token", ""),
               "expires_at": time.time() + float(res.get("expires_in") or 3600)}
        store_credential(payload["name"], json.dumps(cfg))
    except Exception as exc:
        msg = str(exc)
        for secret_value in (locals().get("payload") or {}).get("client_secret", ""), code:
            if secret_value: msg = msg.replace(secret_value, "[hidden]")
        return back("error", msg)
    return back("connected")


# ---------------------------------------------------------------------------
# Google Sheets and Gmail (use an OAuth connection)
# ---------------------------------------------------------------------------
SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
SHEET_RANGE_RE = re.compile(r"^[A-Za-z0-9_ !:'$.\-]{1,100}$")
SHEET_ROW_LIMIT = 10_000


def sheets_action(d: dict, previous: Any) -> Any:
    token = oauth_access_token(str(d.get("credential") or "").strip())
    sheet = render_template(str(d.get("spreadsheet_id", "")), previous).strip()
    rng = render_template(str(d.get("range", "")), previous).strip() or "Sheet1"
    if not SHEET_ID_RE.fullmatch(sheet): raise ValueError("Spreadsheet ID looks wrong. Copy the long ID from the sheet's URL.")
    if not SHEET_RANGE_RE.fullmatch(rng): raise ValueError("Range must look like Sheet1 or Sheet1!A1:D50")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet}/values/{quote(rng, safe='')}"
    op = str(d.get("operation") or "read")
    if op == "read":
        res = do_request("GET", base + "?majorDimension=ROWS", headers)
        rows = (res.get("values") if isinstance(res, dict) else None) or []
        if len(rows) > SHEET_ROW_LIMIT: raise ValueError(f"The range has more than {SHEET_ROW_LIMIT} rows. Use a smaller range.")
        if str(d.get("header", "true")).lower() == "false" or not rows: return rows
        keys = [str(k).strip() for k in rows[0]]
        return [{k: (row[i] if i < len(row) else "") for i, k in enumerate(keys)} for row in rows[1:]]
    if op == "append":
        raw = str(d.get("values") or "").strip()
        if not raw: raise ValueError("Provide the values to append, for example [[\"{{name}}\", \"{{city}}\"]]")
        try: values = json.loads(render_template(raw, previous, json_escape=True))
        except ValueError: raise ValueError("Values must be JSON: a list of rows, or one row as a list")
        if not isinstance(values, list) or not values: raise ValueError("Values must be a non-empty JSON list")
        if not all(isinstance(r, list) for r in values): values = [values]
        if len(values) > 1000 or any(len(r) > 100 for r in values): raise ValueError("At most 1000 rows of 100 cells can be appended at once")
        res = do_request("POST", base + ":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS", headers, json={"values": values})
        upd = (res.get("updates") if isinstance(res, dict) else None) or {}
        return {"updated_range": upd.get("updatedRange"), "updated_rows": upd.get("updatedRows")}
    raise ValueError(f"Unknown Google Sheets action: {op}")


def send_gmail(d: dict, previous: Any) -> dict:
    token = oauth_access_token(str(d.get("credential") or "").strip())
    recipients = [x.strip() for x in render_template(str(d.get("to", "")), previous).split(",") if x.strip()]
    if not 1 <= len(recipients) <= 10: raise ValueError("Provide between 1 and 10 recipients")
    for r in recipients:
        if not EMAIL_RE.fullmatch(r): raise ValueError(f"Invalid recipient address: {r!r}")
    subject = render_template(str(d.get("subject", "")), previous)
    if "\r" in subject or "\n" in subject or len(subject) > 200: raise ValueError("Subject must be one line of at most 200 characters")
    body = render_template(str(d.get("body", "")), previous)
    if len(body) > 100_000: raise ValueError("Email body is too large")
    msg = EmailMessage()
    msg["To"], msg["Subject"] = ", ".join(recipients), subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    do_request("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {"Authorization": f"Bearer {token}"}, json={"raw": raw})
    return {"sent_to": recipients, "subject": subject}


# ---------------------------------------------------------------------------
# AI node: any OpenAI-compatible chat endpoint (Ollama and LM Studio run free on your own computer)
# ---------------------------------------------------------------------------
def run_ai(d: dict, previous: Any) -> Any:
    cred = urlparse(lookup_secret(str(d.get("credential") or "").strip()))
    hint = "The AI credential must look like https://host/v1?model=NAME&key=TOKEN (for a local model: http://127.0.0.1:11434/v1?model=llama3.2)"
    if cred.scheme not in ("http", "https") or not cred.hostname: raise ValueError(hint)
    query = parse_qs(cred.query)
    model, key = query.get("model", [""])[0], query.get("key", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_.:/@\-]{1,100}", model): raise ValueError(hint)
    if cred.scheme == "http" and os.getenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS") != "1":
        raise ValueError("Local models use http, which needs FLOWPILOT_ALLOW_PRIVATE_HOSTS=1 when you start the API. This switch never applies to the HTTP node.")
    assert_public_host(cred.hostname.lower(), relaxed=True)
    prompt = render_template(str(d.get("prompt", "")), previous)
    system = render_template(str(d.get("system", "")), previous)
    if not prompt.strip(): raise ValueError("Write a prompt")
    if len(prompt) > 50_000 or len(system) > 10_000: raise ValueError("The prompt is too long")
    try: temperature = max(0.0, min(float(d.get("temperature") or 0.2), 2.0))
    except (TypeError, ValueError): raise ValueError("Temperature must be a number")
    messages = ([{"role": "system", "content": system}] if system.strip() else []) + [{"role": "user", "content": prompt}]
    base = f"{cred.scheme}://{cred.hostname}{':' + str(cred.port) if cred.port else ''}{cred.path.rstrip('/')}"
    res = do_request("POST", base + "/chat/completions", {"Authorization": f"Bearer {key}"} if key else {},
                     json={"model": model, "messages": messages, "temperature": temperature, "stream": False}, timeout=90.0)
    try: text = str(res["choices"][0]["message"]["content"])
    except (TypeError, KeyError, IndexError): raise ValueError("The AI service returned an unexpected reply")
    if len(text) > 200_000: raise ValueError("The AI reply is too large")
    if str(d.get("json_output") or "false").lower() == "true":
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        try: return json.loads(cleaned)
        except ValueError: raise ValueError("The AI reply was not valid JSON. Ask for JSON only in the prompt.")
    return {"text": text, "model": model}


# ---------------------------------------------------------------------------
# File node: read, write, list and delete files in one folder (backend/data/files)
# ---------------------------------------------------------------------------
FILE_MAX = 5_000_000
FILE_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\- /]{0,150}$")


def files_dir() -> Path:
    return Path(os.getenv("FLOWPILOT_FILES_DIR", str(BASE / "data" / "files"))).resolve()


def file_target(rel: str) -> Path:
    rel = rel.strip().replace("\\", "/")
    if not FILE_PATH_RE.fullmatch(rel) or ".." in rel.split("/") or rel.endswith("/") or "//" in rel:
        raise ValueError("Use a simple relative path such as reports/today.csv")
    folder = files_dir()
    target = (folder / rel).resolve()
    if folder not in target.parents: raise ValueError("Files must stay inside the files folder")
    return target


def file_action(d: dict, previous: Any) -> Any:
    op = str(d.get("operation") or "read")
    if op == "list":
        folder = files_dir()
        if not folder.exists(): return []
        out = []
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                out.append({"path": f.relative_to(folder).as_posix(), "size": f.stat().st_size})
                if len(out) >= 500: break
        return out
    target = file_target(render_template(str(d.get("path", "")), previous))
    rel = target.relative_to(files_dir()).as_posix()
    if op == "read":
        if not target.is_file(): raise ValueError(f"File not found: {rel}")
        if target.stat().st_size > FILE_MAX: raise ValueError("The file is larger than 5 MB")
        raw = target.read_bytes()
        out = {"path": rel, "size": len(raw), "mime": mimetypes.guess_type(rel)[0] or "application/octet-stream"}
        if str(d.get("as") or "text") == "base64": out["base64"] = base64.b64encode(raw).decode()
        else: out["text"] = raw.decode("utf-8", errors="replace")
        return out
    if op == "write":
        content = render_template(str(d.get("content", "")), previous)
        try: raw = base64.b64decode(content, validate=True) if str(d.get("encoding") or "text") == "base64" else content.encode()
        except ValueError: raise ValueError("The content is not valid base64")
        append = str(d.get("mode") or "overwrite") == "append"
        existing = target.stat().st_size if append and target.exists() else 0
        if len(raw) + existing > FILE_MAX: raise ValueError("Files are limited to 5 MB")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "ab" if append else "wb") as fh: fh.write(raw)
        return {"path": rel, "size": target.stat().st_size}
    if op == "delete":
        if not target.is_file(): raise ValueError(f"File not found: {rel}")
        target.unlink()
        return {"path": rel, "deleted": True}
    raise ValueError(f"Unknown file action: {op}")


# ---------------------------------------------------------------------------
# Email-in trigger: poll an IMAP inbox and run the workflow once per new message
# ---------------------------------------------------------------------------
SAMPLE_EMAIL = {"from": "ada@example.com", "to": "you@example.com", "subject": "Sample message", "date": "Mon, 01 Jan 2026 09:00:00 +0000", "text": "Hello from a test email.", "attachments": [], "message_id": "<sample@example.com>"}
MAX_EMAILS_PER_POLL = 10


def inbox_minutes(d: dict) -> int:
    try: minutes = int(float(d.get("minutes", 5)))
    except (TypeError, ValueError): raise ValueError("Check interval must be a number of minutes")
    if not 1 <= minutes <= 1440: raise ValueError("Check interval must be between 1 and 1440 minutes")
    return minutes


def inbox_credential(d: dict) -> str:
    name = str(d.get("credential") or "").strip()
    if not name: raise ValueError("Choose an IMAP credential for the Email trigger")
    return name


def parse_message(raw: bytes) -> dict:
    msg = message_from_bytes(raw, policy=email_policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        try: text = body.get_content()
        except Exception: text = ""
        if body.get_content_type() == "text/html": text = re.sub(r"<[^>]+>", " ", text)
    names = [part.get_filename() or "(unnamed)" for part in msg.iter_attachments()]
    return {"from": str(msg.get("From", "")), "to": str(msg.get("To", "")), "subject": str(msg.get("Subject", ""))[:300], "date": str(msg.get("Date", "")),
            "text": re.sub(r"\s+\n", "\n", text).strip()[:20_000], "attachments": names[:20], "message_id": str(msg.get("Message-ID", ""))}


def fetch_unseen(d: dict) -> list[dict]:
    cred = urlparse(lookup_secret(inbox_credential(d)))
    if cred.scheme not in ("imap", "imaps") or not cred.hostname:
        raise ValueError("The IMAP credential must look like imaps://user:password@imap.example.com:993?mailbox=INBOX (URL-encode special characters)")
    assert_public_host(cred.hostname.lower(), relaxed=True)
    mailbox = parse_qs(cred.query).get("mailbox", ["INBOX"])[0]
    if not re.fullmatch(r"[A-Za-z0-9 _.\-/]{1,80}", mailbox): raise ValueError("Invalid mailbox name")
    want_from, want_subject = str(d.get("from_filter") or "").strip().lower(), str(d.get("subject_filter") or "").strip().lower()
    out = []
    try:
        client = imaplib.IMAP4_SSL(cred.hostname, cred.port or 993, timeout=15) if cred.scheme == "imaps" else imaplib.IMAP4(cred.hostname, cred.port or 143, timeout=15)
        try:
            if cred.scheme == "imap": client.starttls(ssl.create_default_context())
            client.login(unquote(cred.username or ""), unquote(cred.password or ""))
            client.select(mailbox)
            _, found = client.search(None, "UNSEEN")
            for mid in (found[0].split() if found and found[0] else [])[-MAX_EMAILS_PER_POLL:]:
                _, parts = client.fetch(mid, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
                item = parse_message(raw[:2_000_000])
                if want_from and want_from not in item["from"].lower(): continue
                if want_subject and want_subject not in item["subject"].lower(): continue
                client.store(mid, "+FLAGS", "\\Seen")
                out.append(item)
        finally:
            try: client.logout()
            except Exception: pass
    except (imaplib.IMAP4.error, OSError) as exc:
        raise ValueError(f"Could not read the inbox: {str(exc)[:150]}")
    return out


def poll_inbox(doc: dict, node: dict) -> None:
    secrets_ = load_secrets()
    token = _secrets_var.set(secrets_)
    try: messages = fetch_unseen(node.get("data", {}) or {})
    except Exception as exc:
        err = str(redact({"e": str(exc)}, secrets_)["e"])
        def boom(): raise ValueError(err)
        execute_workflow(doc, "email_in", boom)  # recorded as a failed run, so the problem is visible
        return
    finally: _secrets_var.reset(token)
    for m in messages: dispatch(doc, "email_in", m)


# ---------------------------------------------------------------------------
# Repeat until, and parallel execution of branches that are ready together
# ---------------------------------------------------------------------------
MAX_REPEATS = 20
MAX_PARALLEL = 4
PARALLEL_TYPES = {"http", "sql", "rss", "notify", "email", "telegram", "github", "sheets", "gmail", "ai", "code", "delay", "file"}


# ---------------------------------------------------------------------------
# Queue mode: triggers add jobs to a queue and separate worker processes run them
# (python -m app.worker). Start the API with FLOWPILOT_QUEUE=1 to use it.
# ---------------------------------------------------------------------------
MAX_QUEUE = 10_000
STALE_JOB_SECONDS = 900
WEBHOOK_WAIT_SECONDS = 25.0


def queue_mode() -> bool:
    return os.getenv("FLOWPILOT_QUEUE") == "1"


def enqueue(doc: dict, kind: str, initial: Any = None) -> str:
    with conn() as db:
        if db.execute("SELECT COUNT(*) c FROM jobs WHERE status='queued'").fetchone()["c"] >= MAX_QUEUE:
            raise HTTPException(503, "The queue is full. Try again later.")
        job_id = str(uuid.uuid4())
        db.execute("INSERT INTO jobs (id,workflow_id,kind,payload,status,created_at) VALUES (?,?,?,?,'queued',?)", (job_id, doc["id"], kind, json.dumps(initial, default=str), now()))
    return job_id


def dispatch(doc: dict, kind: str, initial: Any = None) -> dict:
    """Run a triggered workflow now, or queue it when queue mode is on."""
    if not queue_mode(): return execute_workflow(doc, kind, initial)
    return {"status": "queued", "job_id": enqueue(doc, kind, initial)}


def claim_job(worker_id: str) -> dict | None:
    """Atomically take the oldest queued job. Two workers can never get the same one."""
    with conn() as db:
        row = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at, id LIMIT 1").fetchone()
        if not row: return None
        if not db.execute("UPDATE jobs SET status='running', claimed_by=?, claimed_at=? WHERE id=? AND status='queued'", (worker_id, now(), row["id"])).rowcount: return None
        return dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())


def run_job(job: dict) -> None:
    try:
        doc = get_workflow(job["workflow_id"])
        result = execute_workflow(doc, job["kind"], json.loads(job["payload"]))
        with conn() as db:
            db.execute("UPDATE jobs SET status='done', finished_at=?, execution_id=?, response=? WHERE id=?",
                       (now(), result["id"], json.dumps(result.get("response")) if result.get("response") else None, job["id"]))
    except Exception as exc:
        log.exception("Job %s failed", job["id"])
        with conn() as db: db.execute("UPDATE jobs SET status='failed', finished_at=?, error=? WHERE id=?", (now(), str(exc)[:300], job["id"]))


def recover_jobs() -> int:
    """A job whose worker vanished is closed as failed (never re-run, so side effects are not repeated)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_SECONDS)).isoformat()
    with conn() as db:
        return db.execute("UPDATE jobs SET status='failed', finished_at=?, error='The worker stopped before this job finished' WHERE status='running' AND claimed_at < ?", (now(), cutoff)).rowcount


def has_respond_node(doc: dict) -> bool:
    return any(n.get("type") == "respond" for n in doc.get("nodes", []))


def wait_for_job(job_id: str, seconds: float = WEBHOOK_WAIT_SECONDS) -> dict | None:
    end = time.time() + seconds
    while time.time() < end:
        with conn() as db: row = db.execute("SELECT status, response, error FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row and row["status"] in ("done", "failed"): return dict(row)
        time.sleep(0.15)
    return None


@app.get("/api/queue")
def queue_stats():
    with conn() as db:
        counts = {r["status"]: r["c"] for r in db.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()}
    return {"mode": "queue" if queue_mode() else "inline", "queued": counts.get("queued", 0), "running": counts.get("running", 0), "failed": counts.get("failed", 0), "done": counts.get("done", 0)}


def step_action(node: dict, previous: Any):
    """Run one node. Returns (displayed_output, value_passed_downstream, branch)."""
    typ = node["type"]
    d = node.get("data", {}) or {}
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
    if typ == "set":
        rendered = render_template(str(d.get("json", "{}")), previous, json_escape=True)
        value = read_json(rendered)
        return value, value, None
    if typ == "filter":
        if not isinstance(previous, list): raise ValueError("Filter List needs a list as input")
        path, op, expected = str(d.get("path") or "").strip(), str(d.get("op", "equals")), str(d.get("equals", ""))
        value = [item for item in previous if compare(get_path(item, path) if path else item, op, expected)]
        return value, value, None
    if typ == "csv":
        value = parse_csv_text(d, previous)
        return value, value, None
    if typ == "merge":
        mode = str(d.get("mode") or "append")
        if mode == "append": value = list(previous)
        elif mode == "first": value = previous[0]
        elif mode == "combine":
            value = {}
            for part in previous:
                if not isinstance(part, dict): raise ValueError("Merge in 'combine' mode needs every input to be an object")
                value.update(part)
        else: raise ValueError(f"Unknown merge mode: {mode}")
        return value, value, None
    if typ == "delay":
        try: seconds = float(d.get("seconds", 1))
        except (TypeError, ValueError): raise ValueError("Delay seconds must be a number")
        seconds = max(0.0, min(seconds, MAX_DELAY_SECONDS))
        time.sleep(seconds)
        return {"waited_seconds": seconds}, previous, None
    if typ == "code":
        value = run_code(d, previous)
        return value, value, None
    if typ == "sql":
        value = run_sql(d, previous)
        return value, value, None
    if typ == "rss":
        value = read_rss(d, previous)
        return value, value, None
    if typ == "ai":
        value = run_ai(d, previous)
        return value, value, None
    if typ == "sticky": return previous, previous, None
    if typ == "file":
        value = file_action(d, previous)
        return value, value, None
    if typ == "telegram": return send_telegram(d, previous), previous, None
    if typ == "sheets":
        value = sheets_action(d, previous)
        return value, value, None
    if typ == "gmail": return send_gmail(d, previous), previous, None
    if typ == "github":
        value = github_action(d, previous)
        return value, value, None
    if typ == "wait":
        seconds = wait_seconds(d)
        if seconds > MAX_WAIT_SECONDS: raise ValueError("Waits longer than 60 seconds must be run by the engine")
        time.sleep(seconds)
        return {"waited_seconds": seconds}, previous, None
    if typ == "respond":
        resp = build_response(d, previous)
        _response_var.get()["response"] = resp
        return resp, previous, None
    if typ == "fail":
        raise ValueError(render_template(str(d.get("message") or "Workflow stopped by a Stop and Fail node"), previous))
    if typ == "switch":
        actual = get_path(previous, str(d.get("path", "")))
        branch = "default"
        for i in "1234":
            wanted = str(d.get("case" + i) or "")
            if wanted != "" and compare(actual, "equals", wanted):
                branch = i
                break
        return {"matched": branch, "value": previous}, previous, branch
    if typ == "condition":
        actual = get_path(previous, str(d.get("path", "")))
        matched = compare(actual, str(d.get("op", "equals")), str(d.get("equals", "")))
        return {"matched": matched, "value": previous}, previous, "true" if matched else "false"
    if typ == "http":
        method = str(d.get("method", "GET")).upper()
        if method not in HTTP_METHODS: raise ValueError(f"Unsupported HTTP method: {method}")
        url = safe_url(render_template(str(d.get("url", "")), previous))
        headers = {k: render_template(v, previous) for k, v in parse_headers(str(d.get("headers", ""))).items()}
        apply_auth(headers, d)
        kwargs: dict[str, Any] = {}
        raw_body = str(d.get("body", "")) if method in ("POST", "PUT", "PATCH") else ""
        if raw_body.strip():
            as_json = render_template(raw_body, previous, json_escape=True)  # values are escaped so quotes cannot break the JSON
            if len(as_json) > 1_000_000: raise ValueError("Request body exceeds 1 MB")
            try: kwargs["json"] = json.loads(as_json)
            except ValueError:
                kwargs["content"] = render_template(raw_body, previous).encode()
                if not any(k.lower() == "content-type" for k in headers): headers["Content-Type"] = "text/plain; charset=utf-8"
        save_as = render_template(str(d.get("save_as") or ""), previous).strip()
        if save_as:
            got_file = do_request(method, url, headers, raw=True, **kwargs)
            target = file_target(save_as)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(got_file["bytes"])
            value = {"path": save_as, "size": len(got_file["bytes"]), "content_type": got_file["content_type"]}
            return value, value, None
        value = do_request(method, url, headers, **kwargs)
        return value, value, None
    if typ == "notify": return send_notify(d, previous), previous, None
    if typ == "email": return send_email(d, previous), previous, None
    if typ == "subflow":
        wid = str(d.get("workflow_id") or "").strip()
        if not wid: raise ValueError("Choose a workflow to run")
        stack = _stack_var.get()
        if wid in stack: raise ValueError("A workflow cannot call itself, directly or indirectly")
        if len(stack) > MAX_SUBFLOW_DEPTH: raise ValueError("Sub-workflows are nested too deeply")
        try: sub = get_workflow(wid)
        except HTTPException: raise ValueError("Sub-workflow not found")
        token, rtoken = _stack_var.set(stack + (wid,)), _response_var.set({})
        try: res = _execute(sub, previous)
        finally:
            _stack_var.reset(token)
            _response_var.reset(rtoken)
        if res["status"] != "success": raise ValueError(f"Sub-workflow '{sub['name']}' failed: {res['error']}")
        return res["output"], res["output"], None
    raise ValueError(f"Unsupported node: {typ}")


# ---------------------------------------------------------------------------
# Execution engine: nodes run when all their live inputs have arrived. Condition branches that are
# not taken deliver "dead" inputs, so a Merge node never waits for a path that will not run.
# ---------------------------------------------------------------------------
class StepFailure(Exception):
    """A node failed; its error step has already been recorded."""


class Graph:
    def __init__(self, doc: dict):
        self.nodes = {n["id"]: n for n in doc["nodes"]}
        if not self.nodes: raise ValueError("Workflow has no nodes")
        self.out: dict[str, list] = {i: [] for i in self.nodes}
        self.inc: dict[str, list] = {i: [] for i in self.nodes}
        for e in doc["edges"]:
            if e["source"] in self.nodes and e["target"] in self.nodes:
                self.out[e["source"]].append(e)
                self.inc[e["target"]].append(e)
        self.steps: list[dict] = []
        self.iter: list[int] = []
        self.suspended: dict | None = None

    def reach(self, starts) -> set:
        seen, queue = set(), list(starts)
        while queue:
            n = queue.pop()
            if n in seen: continue
            seen.add(n)
            queue.extend(e["target"] for e in self.out[n])
        return seen

    def body_of(self, loop_id: str) -> set:
        return self.reach([e["target"] for e in self.out[loop_id] if e.get("sourceHandle") == "each"])

    def has_cycle(self, nodes: set) -> bool:
        indeg = {n: 0 for n in nodes}
        for n in nodes:
            for e in self.out[n]:
                if e["target"] in indeg: indeg[e["target"]] += 1
        queue, seen = [n for n, d in indeg.items() if d == 0], 0
        while queue:
            n = queue.pop()
            seen += 1
            for e in self.out[n]:
                t = e["target"]
                if t in indeg:
                    indeg[t] -= 1
                    if indeg[t] == 0: queue.append(t)
        return seen != len(nodes)


def preview(value: Any) -> Any:
    try: text = json.dumps(value, default=str)
    except (TypeError, ValueError): return str(value)[:2000]
    return {"truncated": True, "preview": text[:2000]} if len(text) > PREVIEW_LIMIT else value


def run_loop(g: Graph, nid: str, node: dict, value: Any):
    data = node.get("data", {}) or {}
    path = str(data.get("path") or "").strip()
    items = get_path(value, path) if path else value
    if not isinstance(items, list): raise ValueError("Loop Over Items needs a list. Use 'List field' to point at one inside the data.")
    if len(items) > MAX_LOOP_ITEMS: raise ValueError(f"A loop is limited to {MAX_LOOP_ITEMS} items")
    outs = g.out[nid]
    if any(e.get("sourceHandle") not in ("each", "done") for e in outs): raise ValueError("Connect the loop's outputs from its 'each' or 'done' handle")
    each = [e for e in outs if e.get("sourceHandle") == "each"]
    done = [e for e in outs if e.get("sourceHandle") == "done"]
    body = g.body_of(nid)
    if body & g.reach([e["target"] for e in done]): raise ValueError("The loop's 'each' and 'done' paths cannot join")
    results = []
    for i, item in enumerate(items):
        g.iter.append(i)
        try: results.append(run_graph(g, body, [(e, item) for e in each]) if each else item)
        finally: g.iter.pop()
    return {"items": len(items)}, results


def run_repeat(g: Graph, nid: str, node: dict, value: Any):
    d = node.get("data", {}) or {}
    try: limit = max(1, min(int(float(d.get("max") or 5)), MAX_REPEATS))
    except (TypeError, ValueError): raise ValueError("Maximum attempts must be a number")
    try: pause = max(0.0, min(float(d.get("pause") or 0), MAX_RETRY_WAIT))
    except (TypeError, ValueError): raise ValueError("Pause must be a number of seconds")
    outs = g.out[nid]
    if any(e.get("sourceHandle") not in ("each", "done") for e in outs): raise ValueError("Connect the node's outputs from its 'each' or 'done' handle")
    each = [e for e in outs if e.get("sourceHandle") == "each"]
    done = [e for e in outs if e.get("sourceHandle") == "done"]
    if not each: raise ValueError("Connect the 'each' handle to the steps to repeat")
    body = g.body_of(nid)
    if body & g.reach([e["target"] for e in done]): raise ValueError("The 'each' and 'done' paths cannot join")
    path, op, expected = str(d.get("path") or "").strip(), str(d.get("op") or "equals"), str(d.get("equals") or "")
    current, met, attempts = value, False, 0
    for i in range(limit):
        attempts = i + 1
        g.iter.append(i)
        try: result = run_graph(g, body, [(e, current) for e in each])
        finally: g.iter.pop()
        if result is not None: current = result
        if compare(get_path(current, path) if path else current, op, expected):
            met = True
            break
        if i < limit - 1 and pause: time.sleep(pause)
    if not met and str(d.get("fail_if_not_met") or "false") == "true": raise ValueError(f"The condition was not met after {limit} attempts")
    return {"attempts": attempts, "condition_met": met}, current


def run_one(g: Graph, nid: str, node: dict, value: Any, step: dict):
    """Run a single node (pin, retries and continue-on-error included). Updates its step and returns (shown, passed, branch)."""
    typ, data = node["type"], node.get("data", {}) or {}
    t0, branch = time.perf_counter(), None
    elapsed = lambda: round((time.perf_counter() - t0) * 1000, 1)
    try:
        pin = str(data.get("pin") or "").strip()
        if pin and typ in PINNABLE:
            shown = passed = read_json(pin)
            step["pinned"] = True
        elif typ == "loop": shown, passed = run_loop(g, nid, node, value)
        elif typ == "repeat": shown, passed = run_repeat(g, nid, node, value)
        else:
            retries = max(0, min(int(float(data.get("retries") or 0)), MAX_RETRIES)) if typ in ERROR_SETTINGS else 0
            wait = max(0.0, min(float(data.get("retry_wait") or 1), MAX_RETRY_WAIT))
            attempt = 0
            while True:
                attempt += 1
                try:
                    shown, passed, branch = step_action(node, value)
                    break
                except StepFailure: raise
                except Exception:
                    if attempt > retries: raise
                    time.sleep(wait)
            if attempt > 1: step["attempts"] = attempt
    except StepFailure as exc:
        step.update(status="error", error=str(exc), duration_ms=elapsed())
        raise
    except Exception as exc:
        if typ in ERROR_SETTINGS and str(data.get("on_error") or "stop") == "continue":
            shown = passed = {"error": str(exc), "input": value}
            step.update(error=str(exc), continued=True)
        else:
            step.update(status="error", error=str(exc), duration_ms=elapsed())
            raise StepFailure(str(exc)) from exc
    step.update(status="error" if step.get("continued") else "success", output=shown, branch=branch, duration_ms=elapsed())
    return shown, passed, branch


def suspend_for_long_wait(g: Graph, nid: str, data: dict, value: Any, step: dict) -> bool:
    """Handle a Wait node. Returns True when the run was paused (a wait over 60 seconds)."""
    try: secs = wait_seconds(data)
    except ValueError as exc:
        step.update(status="error", error=str(exc))
        raise StepFailure(str(exc)) from exc
    if secs <= MAX_WAIT_SECONDS: return False
    if g.iter or len(_stack_var.get()) > 1:
        step.update(status="error", error="A Wait longer than 60 seconds cannot be used inside a loop or a sub-workflow")
        raise StepFailure(step["error"])
    if g.suspended:
        step.update(status="error", error="Only one long Wait per run is supported")
        raise StepFailure(step["error"])
    resume_at = (datetime.now(timezone.utc) + timedelta(seconds=secs)).isoformat()
    g.suspended = {"node_id": nid, "value": value, "resume_at": resume_at}
    step.update(status="waiting", output={"resume_at": resume_at}, duration_ms=0)
    return True


def run_batch(g: Graph, prepared: list) -> list:
    """Run nodes that are ready at the same time. Several network or code nodes run in parallel; the rest run alone."""
    if len(prepared) == 1:
        nid, node, _typ, _data, value, step = prepared[0]
        return [run_one(g, nid, node, value, step)]
    with ThreadPoolExecutor(max_workers=len(prepared)) as pool:
        futures = [pool.submit(contextvars.copy_context().run, run_one, g, p[0], p[1], p[4], p[5]) for p in prepared]
        outcomes = []
        for f in futures:
            try: outcomes.append(f.result())
            except StepFailure as exc: outcomes.append(exc)
    for o in outcomes:
        if isinstance(o, StepFailure): raise o
    return outcomes


def run_graph(g: Graph, allowed: set, seeds: list):
    """Run the nodes in `allowed`. `seeds` are (edge, value) pairs that feed the entry nodes. Returns the last value produced."""
    allowed = set(allowed)
    inner = set()
    for nid in allowed:
        if g.nodes[nid]["type"] in ("loop", "repeat"): inner |= g.body_of(nid)
    allowed -= inner
    pending = {n: sum(1 for e in g.inc[n] if e["source"] in allowed) for n in allowed}
    for edge, _ in seeds: pending[edge["target"]] += 1
    got: dict[str, list] = {n: [] for n in allowed}
    stack: list[str] = []
    final: Any = None

    def deliver(target: str, value: Any, dead: bool) -> None:
        if target not in allowed: return
        pending[target] -= 1
        if not dead: got[target].append(value)
        if pending[target] == 0:
            if got[target]: stack.append(target)
            else:
                for e in g.out[target]: deliver(e["target"], None, True)

    for edge, value in seeds: deliver(edge["target"], value, False)
    while stack:
        batch = [stack.pop()]
        while stack and len(batch) < MAX_PARALLEL and g.nodes[batch[0]]["type"] in PARALLEL_TYPES and g.nodes[stack[-1]]["type"] in PARALLEL_TYPES:
            batch.append(stack.pop())
        prepared = []
        for nid in batch:
            node = g.nodes[nid]
            typ = node["type"]
            data = node.get("data", {}) or {}
            value = got[nid] if typ == "merge" else got[nid][0]
            if len(g.steps) >= MAX_STEPS: raise StepFailure("Maximum execution steps exceeded")
            step = {"node_id": nid, "label": data.get("label", typ), "type": typ, "input": preview(value)}
            if g.iter: step["iteration"] = list(g.iter)
            g.steps.append(step)
            prepared.append((nid, node, typ, data, value, step))
        if len(prepared) == 1 and prepared[0][2] == "wait" and suspend_for_long_wait(g, prepared[0][0], prepared[0][3], prepared[0][4], prepared[0][5]): continue
        outcomes = run_batch(g, prepared)
        for (nid, node, typ, data, value, step), (shown, passed, branch) in zip(prepared, outcomes):
            final = passed if typ in ("condition", "loop", "repeat", "switch") else shown
            # Condition and Switch: only the matching branch is live. Loop and Repeat: only 'done' continues here ('each' runs inside).
            live, dead = [], []
            for e in g.out[nid]:
                handle = e.get("sourceHandle")
                follows = handle in (None, branch) if typ in ("condition", "switch") else handle == "done" if typ in ("loop", "repeat") else True
                (live if follows else dead).append(e)
            for e in dead: deliver(e["target"], None, True)
            for e in reversed(live): deliver(e["target"], passed, False)
    return final


def _execute(doc: dict, initial: Any = None, resume: tuple | None = None) -> dict:
    g = Graph(doc)
    try:
        if resume:
            nid, value = resume
            if nid not in g.nodes: raise ValueError("The waiting node no longer exists in this workflow")
            allowed = g.reach([e["target"] for e in g.out[nid]])
            if g.has_cycle(allowed): raise ValueError("Cycles are not supported in this release")
            final = run_graph(g, allowed, [(e, value) for e in g.out[nid]])
        else:
            starts = [nid for nid, n in g.nodes.items() if n["type"] in START_TYPES]
            if len(starts) != 1: raise ValueError("Workflow must contain exactly one trigger (Manual, Schedule or Webhook)")
            if g.inc[starts[0]]: raise ValueError("A trigger cannot have incoming connections")
            if any(len(g.inc[n]) > 1 and g.nodes[n]["type"] != "merge" for n in g.nodes):
                raise ValueError("Only a Merge node can have more than one incoming connection")
            reachable = g.reach([starts[0]])
            if g.has_cycle(reachable): raise ValueError("Cycles are not supported in this release")
            final = run_graph(g, reachable, [({"target": starts[0]}, initial)])
    except StepFailure as exc:
        return {"status": "failed", "output": None, "steps": g.steps, "error": str(exc)}
    if g.suspended: return {"status": "waiting", "output": final, "steps": g.steps, "error": None, "wait": g.suspended}
    return {"status": "success", "output": final, "steps": g.steps, "error": None}


def run_flow(doc: dict, initial: Any = None, resume: tuple | None = None) -> dict:
    """Run a workflow with credentials available, and hide their values from the result."""
    secrets_ = load_secrets()
    holder: dict = {}
    t1, t2, t3 = _secrets_var.set(secrets_), _stack_var.set((doc.get("id"),)), _response_var.set(holder)
    try: result = _execute(doc, initial, resume)
    finally:
        _secrets_var.reset(t1)
        _stack_var.reset(t2)
        _response_var.reset(t3)
    if result["status"] == "success" and "response" in holder: result["response"] = holder["response"]
    return redact(result, secrets_)


def execute_workflow(doc: dict, kind: str, initial: Any = None) -> dict:
    """Run a workflow and store the execution. `initial` may be a callable so input errors are recorded as failed runs."""
    exec_id, started = str(uuid.uuid4()), now()
    try: result = run_flow(doc, initial() if callable(initial) else initial)
    except Exception as exc: result = {"status": "failed", "output": None, "steps": [], "error": str(exc)}
    with conn() as db:
        if result["status"] == "waiting" and db.execute("SELECT COUNT(*) c FROM waits WHERE status='pending'").fetchone()["c"] >= MAX_PENDING_WAITS:
            result = {"status": "failed", "output": None, "steps": result["steps"], "error": f"Too many runs are waiting (limit {MAX_PENDING_WAITS})"}
        db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                   (exec_id, doc["id"], result["status"], started, None if result["status"] == "waiting" else now(), json.dumps(result["output"]), json.dumps(result["steps"]), result["error"], kind))
        if result["status"] == "waiting": add_wait(db, exec_id, doc["id"], result["wait"])
    if result["status"] == "failed" and kind != "error": run_error_handler(doc, exec_id, kind, result)
    return {"id": exec_id, "workflow_id": doc["id"], "trigger": kind, **result}


def add_wait(db, exec_id: str, workflow_id: str, wait: dict) -> None:
    db.execute("INSERT INTO waits (id,execution_id,workflow_id,node_id,value,resume_at,status,created_at) VALUES (?,?,?,?,?,?,'pending',?)",
               (str(uuid.uuid4()), exec_id, workflow_id, wait["node_id"], json.dumps(wait["value"], default=str), wait["resume_at"], now()))


def resume_due_waits(at: datetime | None = None) -> int:
    """Continue every waiting run whose time has come. Returns how many were resumed."""
    moment = (at or datetime.now(timezone.utc)).isoformat()
    with conn() as db:
        due = db.execute("SELECT id FROM waits WHERE status='pending' AND resume_at <= ? ORDER BY resume_at", (moment,)).fetchall()
    resumed = 0
    for row in due:
        try:
            with conn() as db:  # claim it, so two ticks can never resume the same wait
                claimed = db.execute("UPDATE waits SET status='running' WHERE id=? AND status='pending'", (row["id"],)).rowcount
                w = db.execute("SELECT * FROM waits WHERE id=?", (row["id"],)).fetchone() if claimed else None
                ex = db.execute("SELECT * FROM executions WHERE id=?", (w["execution_id"],)).fetchone() if w else None
            if not w: continue
            if not ex:
                with conn() as db: db.execute("UPDATE waits SET status='done' WHERE id=?", (w["id"],))
                continue
            finish_wait(w, ex)
            resumed += 1
        except Exception:
            log.exception("Resuming a wait failed for %s", row["id"])
    return resumed


def finish_wait(w, ex) -> None:
    old_steps = json.loads(ex["steps"] or "[]")
    for st in old_steps:
        if st.get("node_id") == w["node_id"] and st.get("status") == "waiting":
            st.update(status="success", output={"resumed_at": now()})
    try:
        doc = get_workflow(w["workflow_id"])
        result = run_flow(doc, None, resume=(w["node_id"], json.loads(w["value"])))
    except Exception as exc:
        doc, result = None, {"status": "failed", "output": None, "steps": [], "error": f"Could not resume: {exc}"}
    steps = old_steps + result["steps"]
    with conn() as db:
        db.execute("UPDATE executions SET status=?, finished_at=?, output=?, steps=?, error=? WHERE id=?",
                   (result["status"], None if result["status"] == "waiting" else now(), json.dumps(result["output"]), json.dumps(steps), result["error"], ex["id"]))
        db.execute("UPDATE waits SET status='done' WHERE id=?", (w["id"],))
        if result["status"] == "waiting": add_wait(db, ex["id"], w["workflow_id"], result["wait"])
    if result["status"] == "failed" and doc is not None and ex["trigger_kind"] != "error":
        run_error_handler(doc, ex["id"], ex["trigger_kind"], {**result, "steps": steps})


def recover_waits() -> None:
    """A wait that was being resumed when the API stopped cannot be continued safely, so it is closed as failed."""
    with conn() as db:
        for w in db.execute("SELECT id, execution_id FROM waits WHERE status='running'").fetchall():
            db.execute("UPDATE waits SET status='interrupted' WHERE id=?", (w["id"],))
            db.execute("UPDATE executions SET status='failed', finished_at=?, error='Interrupted: the API stopped while this run was resuming' WHERE id=?", (now(), w["execution_id"]))


@app.post("/api/executions/{execution_id}/cancel")
def cancel_waiting_run(execution_id: str):
    with conn() as db:
        ex = db.execute("SELECT status FROM executions WHERE id=?", (execution_id,)).fetchone()
        if not ex: raise HTTPException(404, "Execution not found")
        if ex["status"] != "waiting": raise HTTPException(400, "Only a waiting run can be cancelled")
        db.execute("UPDATE waits SET status='cancelled' WHERE execution_id=? AND status='pending'", (execution_id,))
        db.execute("UPDATE executions SET status='failed', finished_at=?, error='Cancelled while waiting' WHERE id=?", (now(), execution_id))
    return {"id": execution_id, "cancelled": True}


def run_error_handler(doc: dict, exec_id: str, kind: str, result: dict) -> None:
    """If the trigger names an error workflow, run it with details of the failure. Handlers never trigger handlers."""
    node = start_node(doc)
    handler_id = str(((node or {}).get("data") or {}).get("error_workflow") or "").strip()
    if not handler_id or handler_id == doc["id"]: return
    try:
        handler = get_workflow(handler_id)
        failed = next((st for st in reversed(result["steps"]) if st.get("status") == "error"), {})
        payload = {"workflow": {"id": doc["id"], "name": doc.get("name")}, "execution_id": exec_id, "trigger": kind,
                   "error": result["error"], "failed_node": failed.get("label")}
        execute_workflow(handler, "error", payload)
    except Exception:
        logging.getLogger("flowpilot").exception("error workflow failed")


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
        if node and node["type"] == "email_in": return SAMPLE_EMAIL
        return None
    return execute_workflow(doc, "manual", initial)


# ---------------------------------------------------------------------------
# Activation, schedules and webhooks
# ---------------------------------------------------------------------------
def parse_cron_field(text: str, low: int, high: int, name: str) -> set[int]:
    """One cron field: *, numbers, ranges (a-b), lists (a,b) and steps (*/n, a-b/n)."""
    values: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        try: step = int(step_text) if step_text else 1
        except ValueError: raise ValueError(f"Invalid step in cron {name}: {part!r}")
        if step < 1: raise ValueError(f"Step must be at least 1 in cron {name}")
        if base == "*": start, end = low, high
        elif "-" in base:
            a, _, b = base.partition("-")
            try: start, end = int(a), int(b)
            except ValueError: raise ValueError(f"Invalid range in cron {name}: {part!r}")
        else:
            try: start = int(base)
            except ValueError: raise ValueError(f"Invalid value in cron {name}: {part!r}")
            end = high if step_text else start
        if not (low <= start <= high and low <= end <= high and start <= end): raise ValueError(f"Cron {name} must be between {low} and {high}: {part!r}")
        values.update(range(start, end + 1, step))
    return values


def parse_cron(expr: str) -> dict:
    fields = expr.split()
    if len(fields) != 5: raise ValueError("A cron expression has 5 fields: minute hour day-of-month month day-of-week")
    minute, hour, dom, month, dow = fields
    dow_values = {0 if v == 7 else v for v in parse_cron_field(dow, 0, 7, "day-of-week")}
    return {"minutes": sorted(parse_cron_field(minute, 0, 59, "minute")), "hours": sorted(parse_cron_field(hour, 0, 23, "hour")),
            "dom": parse_cron_field(dom, 1, 31, "day-of-month"), "months": parse_cron_field(month, 1, 12, "month"), "dow": dow_values,
            "dom_any": dom == "*", "dow_any": dow == "*" or dow_values == set(range(7))}


def cron_day_matches(c: dict, day) -> bool:
    if day.month not in c["months"]: return False
    in_dom, in_dow = day.day in c["dom"], ((day.weekday() + 1) % 7) in c["dow"]
    if c["dom_any"] and c["dow_any"]: return True
    if c["dom_any"]: return in_dow
    if c["dow_any"]: return in_dom
    return in_dom or in_dow  # standard cron: when both are restricted, either may match


def cron_next(c: dict, after: datetime) -> datetime:
    local = (after.astimezone() + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for offset in range(0, 1830):  # look up to five years ahead
        day = (local + timedelta(days=offset)).date()
        if not cron_day_matches(c, day): continue
        for hour in c["hours"]:
            for minute in c["minutes"]:
                if offset == 0 and (hour, minute) < (local.hour, local.minute): continue
                return datetime(day.year, day.month, day.day, hour, minute).astimezone().astimezone(timezone.utc)
    raise ValueError("This cron expression never matches a real date")


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
    if mode == "cron":
        cron = parse_cron(str(data.get("cron", "")).strip())
        cron_next(cron, datetime.now(timezone.utc))  # proves it can fire
        return {"mode": "cron", "cron": cron}
    raise ValueError(f"Unknown schedule mode: {mode}")


def next_run_after(cfg: dict, after: datetime) -> datetime:
    if cfg["mode"] == "interval": return after + timedelta(minutes=cfg["minutes"])
    if cfg["mode"] == "cron": return cron_next(cfg["cron"], after)
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
    if node["type"] == "email_in":
        inbox_credential(node.get("data", {}))
        return (( after or datetime.now(timezone.utc)) + timedelta(minutes=inbox_minutes(node.get("data", {})))).isoformat()
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
            if not node or node["type"] not in ("schedule", "email_in"):
                with conn() as db: db.execute("UPDATE workflows SET active=0, next_run_at=NULL WHERE id=?", (row["id"],))
                continue
            if node["type"] == "email_in":
                upcoming = (moment + timedelta(minutes=inbox_minutes(node.get("data", {})))).isoformat()
                with conn() as db: db.execute("UPDATE workflows SET next_run_at=? WHERE id=?", (upcoming, row["id"]))
                poll_inbox(doc, node)
                started += 1
                continue
            # Advance first: if the server was off, a missed run happens once and then the normal rhythm resumes.
            upcoming = next_run_after(validate_schedule(node.get("data", {})), moment).isoformat()
            with conn() as db: db.execute("UPDATE workflows SET next_run_at=? WHERE id=?", (upcoming, row["id"]))
            dispatch(doc, "schedule", {"scheduled_at": moment.isoformat(), "manual": False})
            started += 1
        except Exception:  # one broken workflow must never stop the scheduler
            log.exception("Scheduled run failed for %s", row["id"])
    return started


def scheduler_loop(stop: threading.Event) -> None:
    while not stop.wait(SCHEDULER_TICK_SECONDS):
        try: scheduler_tick()
        except Exception: log.exception("Scheduler tick failed")
        if not queue_mode():  # in queue mode the workers resume waiting runs
            try: resume_due_waits()
            except Exception: log.exception("Resuming waits failed")
        global _last_prune
        if time.time() - _last_prune > 3600:
            _last_prune = time.time()
            try: prune_executions()
            except Exception: log.exception("Pruning failed")


_hook_hits: dict[str, list[float]] = {}


def hook_allowed(token: str) -> bool:
    recent = [t for t in _hook_hits.get(token, []) if time.time() - t < 60]
    if len(recent) >= WEBHOOK_RATE_PER_MINUTE:
        _hook_hits[token] = recent
        return False
    recent.append(time.time())
    _hook_hits[token] = recent
    return True


FORM_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'"
FORM_STYLE = ("body{font:16px system-ui,sans-serif;background:#f6f7f9;margin:0;padding:32px 16px;color:#1b1f27}main{max-width:520px;margin:0 auto;background:#fff;border:1px solid #e3e6ec;border-radius:14px;padding:28px}"
              "h1{margin:0 0 6px;font-size:22px}p{color:#5b6472}label{display:block;margin:16px 0 6px;font-weight:600}input,textarea{box-sizing:border-box;width:100%;padding:10px 12px;border:1px solid #cbd1da;border-radius:8px;font:inherit}"
              "button{margin-top:20px;padding:11px 20px;border:0;border-radius:8px;background:#e9522a;color:#fff;font:inherit;font-weight:600;cursor:pointer}")


def form_fields(node: dict | None) -> list[dict]:
    """The fields of a webhook's optional public form. Invalid definitions simply mean no form."""
    raw = str(((node or {}).get("data") or {}).get("form_fields") or "").strip()
    if not raw: return []
    try: items = json.loads(raw)
    except ValueError: return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict): continue
        name = str(it.get("name") or "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", name): continue
        kind = str(it.get("type") or "text")
        out.append({"name": name, "label": str(it.get("label") or name)[:100], "type": kind if kind in ("text", "email", "number", "textarea") else "text"})
    return out[:20]


def page(title: str, inner: str) -> HTMLResponse:
    doc = f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{html_lib.escape(title)}</title><style>{FORM_STYLE}</style></head><body><main>{inner}</main></body></html>"
    return HTMLResponse(doc, headers={"Content-Security-Policy": FORM_CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})


def render_form(doc: dict, fields: list[dict]) -> HTMLResponse:
    rows = []
    for f in fields:
        n, label = html_lib.escape(f["name"]), html_lib.escape(f["label"])
        control = f"<textarea id='{n}' name='{n}' rows=4></textarea>" if f["type"] == "textarea" else f"<input id='{n}' name='{n}' type='{f['type']}'>"
        rows.append(f"<label for='{n}'>{label}</label>{control}")
    desc = html_lib.escape(doc.get("description") or "")
    return page(doc.get("name", "Form"), f"<h1>{html_lib.escape(doc.get('name', 'Form'))}</h1><p>{desc}</p><form method=post action=''>{''.join(rows)}<button type=submit>Submit</button></form>")


def form_done_page(doc: dict, ok: bool) -> HTMLResponse:
    return page(doc.get("name", "Form"), f"<h1>{'Thank you' if ok else 'Something went wrong'}</h1><p>{'Your response was received.' if ok else 'Your response could not be processed. Please try again later.'}</p>")


def lookup_hook(token: str) -> dict | None:
    with conn() as db:
        row = db.execute("SELECT * FROM workflows WHERE webhook_token=? AND active=1", (token,)).fetchone()
    return workflow_row(row) if row else None


@app.api_route("/api/hooks/{token}", methods=["GET", "POST"])
async def receive_webhook(token: str, request: Request):
    """Public endpoint: the secret token in the URL is the credential. Only active webhook workflows respond."""
    doc = await run_in_threadpool(lookup_hook, token)
    if not doc or trigger_kind(doc) != "webhook": raise HTTPException(404, "Not found")
    if not hook_allowed(token): raise HTTPException(429, "Too many requests")
    fields = form_fields(start_node(doc))
    if request.method == "GET" and fields: return render_form(doc, fields)
    if int(request.headers.get("content-length") or 0) > WEBHOOK_MAX_BODY: raise HTTPException(413, "Payload too large")
    raw = await request.body()
    if len(raw) > WEBHOOK_MAX_BODY: raise HTTPException(413, "Payload too large")
    ctype = request.headers.get("content-type", "")
    body: Any = None
    if raw:
        text = raw.decode("utf-8", errors="replace")
        if ctype.split(";")[0].strip() == "application/x-www-form-urlencoded":
            sent = {k: v[0][:5000] for k, v in parse_qs(text, keep_blank_values=True).items()}
            body = {f["name"]: sent.get(f["name"], "") for f in fields} if fields else sent
        else:
            try: body = json.loads(text)
            except ValueError: body = text
    is_form = ctype.split(";")[0].strip() == "application/x-www-form-urlencoded" and "text/html" in request.headers.get("accept", "")
    payload = webhook_payload(request.method, dict(request.query_params), body, ctype.split(";")[0].strip())
    if queue_mode():
        job_id = await run_in_threadpool(enqueue, doc, "webhook", payload)
        if has_respond_node(doc):
            done = await run_in_threadpool(wait_for_job, job_id)
            if done and done["status"] == "done" and done["response"]:
                reply = json.loads(done["response"])
                return JSONResponse(reply["body"], status_code=reply["status"])
            if done and done["status"] == "failed": return JSONResponse({"status": "failed", "job_id": job_id}, status_code=500)
        if is_form: return form_done_page(doc, True)
        return JSONResponse({"status": "queued", "job_id": job_id}, status_code=202)
    result = await run_in_threadpool(execute_workflow, doc, "webhook", payload)
    if result["status"] == "success" and result.get("response"):
        return JSONResponse(result["response"]["body"], status_code=result["response"]["status"])
    code = 200 if result["status"] in ("success", "waiting") else 500
    if is_form: return form_done_page(doc, result["status"] != "failed")
    return JSONResponse({"status": result["status"], "execution_id": result["id"]}, status_code=code)


# ---------------------------------------------------------------------------
# Run-history retention, backups
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS = {"retention_days": 90, "max_executions": 5000}
_last_prune = 0.0


def get_settings() -> dict:
    with conn() as db:
        rows = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}
    out = dict(DEFAULT_SETTINGS)
    for k in out:
        try: out[k] = int(rows[k])
        except (KeyError, ValueError): pass
    return out


def prune_executions() -> int:
    """Delete old finished runs (never one that is still waiting). Returns how many were removed."""
    cfg = get_settings()
    removed = 0
    with conn() as db:
        if cfg["retention_days"] > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=cfg["retention_days"])).isoformat()
            removed += db.execute("DELETE FROM executions WHERE status != 'waiting' AND started_at < ?", (cutoff,)).rowcount
        if cfg["max_executions"] > 0:
            removed += db.execute("DELETE FROM executions WHERE status != 'waiting' AND id NOT IN (SELECT id FROM executions WHERE status != 'waiting' ORDER BY started_at DESC LIMIT ?)", (cfg["max_executions"],)).rowcount
        db.execute("DELETE FROM waits WHERE execution_id NOT IN (SELECT id FROM executions)")
        db.execute("DELETE FROM jobs WHERE status IN ('done','failed') AND finished_at < ?", ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
    return removed


class SettingsIn(BaseModel):
    retention_days: int = Field(ge=0, le=3650)
    max_executions: int = Field(ge=0, le=100_000)


@app.get("/api/settings")
def read_settings():
    with conn() as db:
        total = db.execute("SELECT COUNT(*) c FROM executions").fetchone()["c"]
    return {**get_settings(), "executions_stored": total}


@app.put("/api/settings")
def write_settings(body: SettingsIn):
    with conn() as db:
        for k, v in body.model_dump().items():
            db.execute("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    return {**get_settings(), "removed": prune_executions()}


@app.post("/api/settings/prune")
def prune_now():
    return {"removed": prune_executions()}


@app.get("/api/backup/workflows")
def backup_workflows():
    with conn() as db:
        rows = db.execute("SELECT definition FROM workflows ORDER BY name").fetchall()
    items = []
    for r in rows:
        d = json.loads(r["definition"])
        items.append({k: d.get(k) for k in ("name", "description", "tags", "nodes", "edges")})
    body = {"format": "flowpilot-backup", "version": 1, "exported_at": now(), "workflows": items}
    return JSONResponse(body, headers={"Content-Disposition": 'attachment; filename="flowpilot-workflows.json"'})


class RestoreIn(BaseModel):
    format: str
    workflows: list[dict[str, Any]] = Field(max_length=200)


@app.post("/api/backup/restore")
def restore_workflows(body: RestoreIn):
    """Add the workflows from a backup file as new, inactive workflows. Nothing existing is overwritten."""
    if body.format != "flowpilot-backup": raise HTTPException(400, "This is not a FlowPilot backup file")
    created, skipped = [], []
    for item in body.workflows:
        label = str(item.get("name") or "(unnamed)")[:120]
        try:
            wf = WorkflowIn(name=item.get("name") or "Restored workflow", description=item.get("description") or "", nodes=item.get("nodes") or [], edges=item.get("edges") or [], tags=item.get("tags") or [])
            validate_flow(wf)
        except HTTPException as exc: skipped.append({"name": label, "reason": exc.detail}); continue
        except Exception as exc: skipped.append({"name": label, "reason": str(exc).splitlines()[0][:150]}); continue
        wid = str(uuid.uuid4())
        doc = wf.model_dump()
        doc["id"] = wid
        with conn() as db: insert_workflow(db, wid, wf.name, wf.description, doc, now())
        created.append({"id": wid, "name": wf.name})
    return {"created": created, "skipped": skipped}


@app.get("/api/backup/database")
def backup_database():
    """A consistent copy of the whole database (workflows, runs, users, encrypted credentials). The key file is not included."""
    tmp = Path(tempfile.mkdtemp(prefix="flowpilot-backup-")) / "flowpilot-backup.db"
    src, dst = conn(), sqlite3.connect(tmp)
    try:
        src.backup(dst)
        dst.execute("DELETE FROM sessions")
        dst.execute("DELETE FROM oauth_pending")
        dst.commit()
    finally:
        src.close()
        dst.close()
    return FileResponse(tmp, filename="flowpilot-backup.db", media_type="application/octet-stream", background=BackgroundTask(lambda: (tmp.unlink(missing_ok=True), tmp.parent.rmdir())))


@app.get("/api/executions")
def executions(request: Request, workflow_id: str | None = None):
    visible = visible_workflow_ids(getattr(request.state, "actor", None))
    if visible is not None and workflow_id and workflow_id not in visible: return []
    with conn() as db:
        if workflow_id:
            rows=db.execute("SELECT * FROM executions WHERE workflow_id=? ORDER BY started_at DESC LIMIT 100",(workflow_id,)).fetchall()
        else: rows=db.execute("SELECT * FROM executions ORDER BY started_at DESC LIMIT 1000").fetchall()
        if visible is not None: rows = [r for r in rows if r["workflow_id"] in visible]
        rows = rows[:100]
        resume = {w["execution_id"]: w["resume_at"] for w in db.execute("SELECT execution_id, resume_at FROM waits WHERE status='pending'").fetchall()}
    return [{"id":r["id"],"workflow_id":r["workflow_id"],"status":r["status"],"started_at":r["started_at"],"error":r["error"],"trigger":r["trigger_kind"],"steps":json.loads(r["steps"] or "[]"),"output":json.loads(r["output"] or "null"),"resume_at":resume.get(r["id"])} for r in rows]
