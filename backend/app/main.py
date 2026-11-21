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
import shutil
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
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Literal
from email import message_from_bytes, policy as email_policy
from email.parser import BytesParser
from email.message import EmailMessage
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse

import httpx
from .n8n_import import convert as convert_n8n
from .make_import import convert as convert_make
from . import connectors as connector_lib
from .connector_library import LIBRARY as CONNECTOR_LIBRARY
from .oidc import verify_id_token
from .pgcompat import PgConnection
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

BASE = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("FLOWPILOT_DB", str(BASE / "flowpilot.db")))
MAX_NODES = 60
MAX_DELAY_SECONDS = 5.0
START_TYPES = {"trigger", "schedule", "webhook", "email_in", "poll_in"}
SCHEDULER_ENABLED = os.getenv("FLOWPILOT_SCHEDULER", "1") != "0"
SCHEDULER_TICK_SECONDS = 10
WEBHOOK_MAX_BODY = 256 * 1024
WEBHOOK_RATE_PER_MINUTE = int(os.getenv("FLOWPILOT_WEBHOOK_RATE", "60"))  # per webhook URL
log = logging.getLogger("flowpilot")


_scheduler_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(_app):
    global _scheduler_thread
    stop = threading.Event()
    recover_waits()
    if SCHEDULER_ENABLED:
        _scheduler_thread = threading.Thread(target=scheduler_loop, args=(stop,), daemon=True, name="flowpilot-scheduler")
        _scheduler_thread.start()
    yield
    stop.set()


VERSION = "0.43.0"
app = FastAPI(title="FlowPilot API", version=VERSION, description="Local visual automation engine", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=False, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])


def now(): return datetime.now(timezone.utc).isoformat()

DATABASE_URL = os.getenv("FLOWPILOT_DATABASE_URL", "").strip()
if DATABASE_URL and not DATABASE_URL.startswith(("postgresql://", "postgres://")): raise SystemExit("FLOWPILOT_DATABASE_URL must start with postgresql://")


def conn():
    if DATABASE_URL: return PgConnection(DATABASE_URL)
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
        if "replay_of" not in {r["name"] for r in db.execute("PRAGMA table_info(executions)")}:
            db.execute("ALTER TABLE executions ADD COLUMN replay_of TEXT")
        if "ai_usage" not in {r["name"] for r in db.execute("PRAGMA table_info(executions)")}:
            db.execute("ALTER TABLE executions ADD COLUMN ai_usage TEXT")
        db.execute("CREATE TABLE IF NOT EXISTS workflow_tests (id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, name TEXT NOT NULL, spec TEXT NOT NULL, created_at TEXT NOT NULL)")
        for r in db.execute("SELECT id FROM workflows WHERE webhook_token IS NULL").fetchall():
            db.execute("UPDATE workflows SET webhook_token=? WHERE id=?", (new_token(), r["id"]))
        db.execute("CREATE TABLE IF NOT EXISTS credentials (name TEXT PRIMARY KEY, value_enc TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS project_members (project_id TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY (project_id, user_id))")
        if "project_id" not in {r["name"] for r in db.execute("PRAGMA table_info(workflows)")}: db.execute("ALTER TABLE workflows ADD COLUMN project_id TEXT")
        if "project_id" not in {r["name"] for r in db.execute("PRAGMA table_info(credentials)")}: db.execute("ALTER TABLE credentials ADD COLUMN project_id TEXT")
        if "auth_source" not in {r["name"] for r in db.execute("PRAGMA table_info(users)")}: db.execute("ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'local'")
        if "role" not in {r["name"] for r in db.execute("PRAGMA table_info(users)")}:
            db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
            first = db.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
            if first: db.execute("UPDATE users SET role='owner' WHERE id=?", (first["id"],))
        db.execute("CREATE TABLE IF NOT EXISTS workflow_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL, saved_at TEXT NOT NULL, saved_by TEXT, name TEXT NOT NULL, definition TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS waits (id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, workflow_id TEXT NOT NULL, node_id TEXT NOT NULL, value TEXT NOT NULL, resume_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL)")
        if "kind" not in {r["name"] for r in db.execute("PRAGMA table_info(waits)")}: db.execute("ALTER TABLE waits ADD COLUMN kind TEXT NOT NULL DEFAULT 'timer'")
        db.execute("CREATE TABLE IF NOT EXISTS oauth_pending (state TEXT PRIMARY KEY, payload_enc TEXT NOT NULL, created_at REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS data_rows (id INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL)")
        if "project_id" not in {r["name"] for r in db.execute("PRAGMA table_info(data_rows)")}: db.execute("ALTER TABLE data_rows ADD COLUMN project_id TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_data_rows_tbl ON data_rows (tbl, id)")
        db.execute("CREATE TABLE IF NOT EXISTS connectors (name TEXT PRIMARY KEY, manifest TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS variables (name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS poll_seen (workflow_id TEXT NOT NULL, item_id TEXT NOT NULL, seen_at TEXT NOT NULL, PRIMARY KEY (workflow_id, item_id))")
        db.execute("CREATE TABLE IF NOT EXISTS hook_keys (workflow_id TEXT NOT NULL, key TEXT NOT NULL, seen_at TEXT NOT NULL, PRIMARY KEY (workflow_id, key))")
        db.execute("CREATE TABLE IF NOT EXISTS poll_state (workflow_id TEXT PRIMARY KEY, initialized INTEGER NOT NULL DEFAULT 0)")
        db.execute("CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, username TEXT, role TEXT, method TEXT NOT NULL, path TEXT NOT NULL, status INTEGER NOT NULL)")
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
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("settings")
    @classmethod
    def clean_settings(cls, value: dict) -> dict:
        out: dict[str, Any] = {}
        if "timeout_seconds" in value and value["timeout_seconds"] not in (None, "", 0, "0"):
            try: secs = int(float(value["timeout_seconds"]))
            except (TypeError, ValueError): raise ValueError("The time limit must be a number of seconds")
            if not 5 <= secs <= 3600: raise ValueError("The time limit must be between 5 and 3600 seconds")
            out["timeout_seconds"] = secs
        if value.get("ai_call_limit") not in (None, "", 0, "0"):
            try: calls = int(float(value["ai_call_limit"]))
            except (TypeError, ValueError): raise ValueError("The AI call limit must be a number")
            if not 1 <= calls <= 500: raise ValueError("The AI call limit must be between 1 and 500 per run")
            out["ai_call_limit"] = calls
        if value.get("ai_token_limit") not in (None, "", 0, "0"):
            try: toks = int(float(value["ai_token_limit"]))
            except (TypeError, ValueError): raise ValueError("The AI token limit must be a number")
            if not 1000 <= toks <= 5_000_000: raise ValueError("The AI token limit must be between 1000 and 5000000 per run")
            out["ai_token_limit"] = toks
        mode = str(value.get("save_runs") or "all")
        if mode not in ("all", "failures", "none"): raise ValueError("Save runs must be all, failures or none")
        if mode != "all": out["save_runs"] = mode
        return out

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
                           "loop", "merge", "set", "filter", "csv", "notify", "email", "subflow", "switch", "code", "fail", "sql", "rss", "telegram", "github", "wait", "respond", "sheets", "gmail", "sticky", "ai", "repeat", "file", "app", "agent", "callback", "sort", "limit", "dedupe", "split", "aggregate", "rename", "datetime", "crypto", "convert", "table"}
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
    if len(kinds) == 1 and kinds[0] in ("schedule", "webhook", "email_in", "poll_in"): return kinds[0]
    return "manual"

# ---------------------------------------------------------------------------
# Authentication: one owner account, PBKDF2 password hashing, server-side sessions
# ---------------------------------------------------------------------------
COOKIE = "fp_session"
SESSION_DAYS = 7
PBKDF2_ROUNDS = 240_000
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300
PUBLIC_PATHS = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup", "/api/auth/logout", "/api/auth/sso/start", "/api/auth/sso/callback"}
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


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"


def create_session(user_id: str, response: Response, secure: bool = False) -> None:
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc)
    expires = created.timestamp() + SESSION_DAYS * 86400
    with conn() as db:
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (created.isoformat(),))
        db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (token_hash(token), user_id, created.isoformat(), datetime.fromtimestamp(expires, timezone.utc).isoformat()))
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict", path="/", secure=secure)


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
        if n.get("type") == "agent":
            for wid in str((n.get("data") or {}).get("workflows") or "").split(","):
                if wid.strip() and not can_see_workflow(actor, wid.strip()):
                    raise HTTPException(403, "You do not have access to a workflow chosen in an AI Agent node")


ROLES = ("owner", "admin", "editor", "viewer")
ASSIGNABLE_ROLES = ("admin", "editor", "viewer")
SELF_SERVICE = {"/api/auth/password"}


def allowed(role: str, method: str, path: str) -> bool:
    """Roles: viewer reads, editor also builds and runs workflows, admin also manages credentials, owner also manages users."""
    if path in SELF_SERVICE: return True
    if path.startswith(("/api/users", "/api/backup", "/api/audit", "/api/sso", "/api/metrics")): return role == "owner"
    if path.startswith("/api/projects") and method not in ("GET", "HEAD"): return role == "owner"
    if method in ("GET", "HEAD"): return True
    if path.startswith(("/api/credentials", "/api/oauth", "/api/settings", "/api/variables", "/api/connectors")): return role in ("owner", "admin")
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
    if path.startswith("/api/") and path not in PUBLIC_PATHS and path != "/api/oauth/callback" and not path.startswith(("/api/hooks/", "/api/resume/")) and request.method != "OPTIONS":
        user = session_user(request.cookies.get(COOKIE))
        if not user:
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        if not allowed(user["role"], request.method, path):
            return JSONResponse({"detail": "Your role does not allow this action"}, status_code=403)
        request.state.user = user["username"]
        request.state.actor = user
        if user["role"] not in ("owner", "admin"):
            wf = re.match(r"^/api/workflows/([^/]+)", path)
            ex = re.match(r"^/api/executions/([^/]+)/(?:cancel|replay|respond|explain)$", path)
            target = wf.group(1) if wf else None
            if ex:
                with conn() as db: row = db.execute("SELECT workflow_id FROM executions WHERE id=?", (ex.group(1),)).fetchone()
                target = row["workflow_id"] if row else None
            if target and not can_see_workflow(user, target):
                return JSONResponse({"detail": "Not found"}, status_code=404)
    response = await call_next(request)
    user = getattr(request.state, "actor", None)
    login = path == "/api/auth/login"
    if path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS") and (response.status_code < 400 or (login and response.status_code in (401, 429))) and not path.startswith(("/api/hooks/", "/api/resume/")) and path != "/api/oauth/callback":
        try: record_audit(getattr(request.state, "audit_user", None) or (user["username"] if user else None), user["role"] if user else None, request.method, path, response.status_code)
        except Exception: log.exception("Could not write the audit log")
    return response


AUDIT_KEEP = 5000


def record_audit(username: str | None, role: str | None, method: str, path: str, status: int) -> None:
    with conn() as db:
        cur = db.execute("INSERT INTO audit_log (at,username,role,method,path,status) VALUES (?,?,?,?,?,?)", (now(), username, role, method, path[:300], status))
        db.execute("DELETE FROM audit_log WHERE id <= ?", (cur.lastrowid - AUDIT_KEEP,))


@app.post("/api/import/n8n")
def import_n8n(body: dict[str, Any]):
    """Convert an n8n export into a FlowPilot workflow. Nothing is saved: the caller creates the workflow from the result."""
    try: return convert_n8n(body)
    except ValueError as exc: raise HTTPException(400, str(exc))


@app.post("/api/import/make")
def import_make(body: dict[str, Any]):
    """Convert a Make scenario blueprint into a FlowPilot workflow. Nothing is saved: the caller creates the workflow from the result."""
    try: return convert_make(body)
    except ValueError as exc: raise HTTPException(400, str(exc))


@app.get("/api/audit")
def audit_log(limit: int = 200, offset: int = 0, who: str = ""):
    limit, offset = max(1, min(limit, 1000)), max(0, offset)
    with conn() as db:
        if who: rows = db.execute("SELECT * FROM audit_log WHERE username=? ORDER BY id DESC LIMIT ? OFFSET ?", (who, limit, offset)).fetchall()
        else: rows = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [dict(r) for r in rows]


MAX_BODY = 5 * 1024 * 1024
MAX_RESTORE_BODY = 25 * 1024 * 1024


@app.middleware("http")
async def harden(request: Request, call_next):
    """Cap request sizes and add basic security headers to every response."""
    path = request.url.path
    is_api = path.startswith("/api/")
    if is_api and request.method in ("POST", "PUT", "PATCH") and not path.startswith(("/api/hooks/", "/api/resume/")):
        try: length = int(request.headers.get("content-length") or 0)
        except ValueError: length = 0
        if length > (MAX_RESTORE_BODY if path == "/api/backup/restore" else MAX_BODY):
            return JSONResponse({"detail": "Request too large"}, status_code=413)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if is_api and not path.startswith("/api/hooks/"): response.headers.setdefault("Cache-Control", "no-store")  # resume links are never cached either
    return response


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
    sso = sso_config()
    return {"setup_required": not has_users, "authenticated": user is not None, "username": user["username"] if user else None, "role": user["role"] if user else None, "projects": projects,
            "sso": {"enabled": sso["ready"], "label": "single sign-on"}}


@app.post("/api/auth/setup", status_code=201)
def auth_setup(body: SetupIn, request: Request, response: Response):
    """Create the owner account. Only possible while no account exists."""
    user_id = str(uuid.uuid4())
    with conn() as db:
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "An account already exists. Please sign in.")
        db.execute("INSERT INTO users (id,username,password_hash,created_at,role) VALUES (?,?,?,?,?)", (user_id, body.username.lower(), hash_password(body.password), now(), "owner"))
    create_session(user_id, response, is_https(request))
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
        request.state.audit_user = body.username.strip().lower()[:40] if row else "(unknown user)"  # a mistyped password is never recorded as a name
        raise HTTPException(401, "Incorrect username or password")
    _failed_logins.pop(key, None)
    request.state.audit_user = body.username.strip().lower()
    create_session(row["id"], response, is_https(request))
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
        rows = db.execute("SELECT id, username, role, created_at, auth_source FROM users ORDER BY created_at").fetchall()
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
        if body.password is not None and db.execute("SELECT auth_source FROM users WHERE id=?", (user_id,)).fetchone()["auth_source"] == "sso": raise HTTPException(400, "A single sign-on account has no password")
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
        row = db.execute("SELECT password_hash, auth_source FROM users WHERE id=?", (user["id"],)).fetchone()
        if row["auth_source"] == "sso": raise HTTPException(400, "A single sign-on account has no password")
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
        if db.execute("SELECT 1 FROM credentials WHERE project_id=? LIMIT 1", (project_id,)).fetchone(): raise HTTPException(400, "Share or delete the project's credentials first")
        db.execute("DELETE FROM project_members WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM projects WHERE id=?", (project_id,))


@app.get("/api/health")
def health():
    try:
        with conn() as db: db.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception: db_ok = False
    scheduler = "off" if not SCHEDULER_ENABLED else "running" if _scheduler_thread is not None and _scheduler_thread.is_alive() else "stopped"
    return {"status": "ok" if db_ok else "degraded", "version": VERSION, "mode": "local", "database": "postgres" if DATABASE_URL else "sqlite", "queue": queue_mode(), "scheduler": scheduler}

def search_text(doc: dict) -> str:
    """Lower-case words from node types and the text typed into node settings, so the Overview search can find a workflow by what it does."""
    parts: list[str] = []
    for n in doc.get("nodes") or []:
        parts.append(str(n.get("type") or ""))
        for v in (n.get("data") or {}).values():
            if isinstance(v, str) and v and len(v) < 500: parts.append(v)
    return " ".join(parts).lower()[:6000]


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
        out.append({"id": r["id"], "name": r["name"], "description": r["description"], "updated_at": r["updated_at"], "tags": doc.get("tags", []), "search": search_text(doc),
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
    return json.dumps({k: doc.get(k) for k in ("name", "description", "nodes", "edges", "settings")}, sort_keys=True)


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
        if "settings" not in body.model_fields_set: doc["settings"] = json.loads(row["definition"]).get("settings", {})
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
        db.execute("DELETE FROM workflow_tests WHERE workflow_id=?", (workflow_id,))
        db.execute("DELETE FROM waits WHERE workflow_id=?", (workflow_id,))
        db.execute("DELETE FROM poll_seen WHERE workflow_id=?", (workflow_id,))
        db.execute("DELETE FROM poll_state WHERE workflow_id=?", (workflow_id,))


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
        old_tests = db.execute("SELECT name, spec FROM workflow_tests WHERE workflow_id=?", (workflow_id,)).fetchall()
        for t in old_tests: db.execute("INSERT INTO workflow_tests (id,workflow_id,name,spec,created_at) VALUES (?,?,?,?,?)", (str(uuid.uuid4()), new_id, t["name"], t["spec"], now()))
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
_vars_var: contextvars.ContextVar[dict] = contextvars.ContextVar("fp_vars", default={})
_run_var: contextvars.ContextVar[dict] = contextvars.ContextVar("fp_run", default={})
_live_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar("fp_live", default=None)
_stack_var: contextvars.ContextVar[tuple] = contextvars.ContextVar("fp_call_stack", default=())


def key_path() -> Path:
    return Path(os.getenv("FLOWPILOT_KEY_FILE", str(DB_PATH.parent / "flowpilot.key")))


def write_key_file(path: Path, keys: list[bytes]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(b"\n".join(keys) + b"\n")
    try: os.chmod(tmp, 0o600)
    except OSError: pass  # best effort on Windows
    os.replace(tmp, path)


def fernet() -> Fernet:
    """The encryption key file holds one key per line: the first encrypts, every key can decrypt (which makes rotation crash-safe)."""
    path = key_path()
    cached = _fernets.get(str(path))
    if cached is None:
        if path.exists(): keys = [k for k in path.read_bytes().split() if k]
        else:
            keys = [Fernet.generate_key()]
            write_key_file(path, keys)
        cached = _fernets[str(path)] = MultiFernet([Fernet(k) for k in keys])
    return cached


def rotate_encryption_key(drop_old: bool = False) -> int:
    """Create a new key, re-encrypt every credential with it, and optionally forget the old keys. Returns how many credentials were re-encrypted."""
    fernet()  # makes sure a key file exists
    path = key_path()
    old_keys = [k for k in path.read_bytes().split() if k]
    write_key_file(path, [Fernet.generate_key()] + old_keys)  # new key first; old keys still decrypt, so a crash here loses nothing
    _fernets.pop(str(path), None)
    f = fernet()
    with conn() as db:
        rows = db.execute("SELECT name, value_enc FROM credentials").fetchall()
        for r in rows: db.execute("UPDATE credentials SET value_enc=? WHERE name=?", (f.rotate(r["value_enc"].encode()).decode(), r["name"]))
    if drop_old:
        write_key_file(path, [path.read_bytes().split()[0]])
        _fernets.pop(str(path), None)
    return len(rows)


ALL_PROJECTS = "*"


def workflow_project(doc: dict) -> str | None:
    with conn() as db: row = db.execute("SELECT project_id FROM workflows WHERE id=?", (doc.get("id"),)).fetchone()
    return row["project_id"] if row else None


def load_secrets(project: str | None = ALL_PROJECTS) -> dict[str, str | None]:
    """Credentials a run may use. A credential with no project is shared by every workflow; one with a project only by workflows of that project.
    Pass ALL_PROJECTS (the default) for internal uses such as webhook secrets, or a workflow's own project id (None when it has none)."""
    with conn() as db:
        if project == ALL_PROJECTS: rows = db.execute("SELECT name, value_enc FROM credentials").fetchall()
        elif project is None: rows = db.execute("SELECT name, value_enc FROM credentials WHERE project_id IS NULL").fetchall()
        else: rows = db.execute("SELECT name, value_enc FROM credentials WHERE project_id IS NULL OR project_id=?", (project,)).fetchall()
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
    project_id: str | None = Field(default=None, max_length=100)  # leave out to keep the current scope; null shares it with every workflow


class CredentialScopeIn(BaseModel):
    project_id: str | None = Field(default=None, max_length=100)


@app.get("/api/credentials")
def list_credentials(request: Request):
    """Names only. Values are never returned by any endpoint. People outside a project do not see that project's credentials."""
    actor = getattr(request.state, "actor", None)
    mine = None if sees_everything(actor) else my_project_ids(actor)
    with conn() as db:
        rows = db.execute("SELECT name, value_enc, created_at, updated_at, project_id FROM credentials ORDER BY name").fetchall()
        names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM projects").fetchall()}
    out = []
    for r in rows:
        if mine is not None and r["project_id"] is not None and r["project_id"] not in mine: continue
        try: cfg = oauth_config(fernet().decrypt(r["value_enc"].encode()).decode())
        except InvalidToken: cfg = None
        out.append({"name": r["name"], "project_id": r["project_id"], "project_name": names.get(r["project_id"]), "created_at": r["created_at"], "updated_at": r["updated_at"], "kind": "oauth2" if cfg else "secret", "provider": cfg.get("provider") if cfg else None})
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
        scope = body.project_id or None
        if "project_id" in body.model_fields_set and scope and not db.execute("SELECT 1 FROM projects WHERE id=?", (scope,)).fetchone(): raise HTTPException(404, "Project not found")
        ts = now()
        if exists:
            db.execute("UPDATE credentials SET value_enc=?, updated_at=? WHERE name=?", (encrypted, ts, name))
            if "project_id" in body.model_fields_set: db.execute("UPDATE credentials SET project_id=? WHERE name=?", (scope, name))
        else: db.execute("INSERT INTO credentials (name,value_enc,created_at,updated_at,project_id) VALUES (?,?,?,?,?)", (name, encrypted, ts, ts, scope))
    return {"name": name, "saved": True}


@app.put("/api/credentials/{name}/project")
def set_credential_project(name: str, body: CredentialScopeIn):
    """Change who may use a credential without retyping its value: one project, or null for every workflow."""
    scope = body.project_id or None
    with conn() as db:
        if not db.execute("SELECT 1 FROM credentials WHERE name=?", (name,)).fetchone(): raise HTTPException(404, "Credential not found")
        if scope and not db.execute("SELECT 1 FROM projects WHERE id=?", (scope,)).fetchone(): raise HTTPException(404, "Project not found")
        db.execute("UPDATE credentials SET project_id=? WHERE name=?", (scope, name))
    return {"name": name, "project_id": scope}


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
        elif path.startswith("run.") and not (isinstance(data, dict) and "run" in data):
            info = _run_var.get()
            if path[4:] not in info: raise ValueError(f"{path!r} is not available. Use run.approve_url, run.reject_url or run.resume_url.")
            value = info[path[4:]]
        elif path.startswith("vars.") and not (isinstance(data, dict) and "vars" in data):
            variables = _vars_var.get()
            if path[5:] not in variables: raise ValueError(f"The variable {path[5:]!r} is not defined. Add it in Settings, under Variables.")
            value = variables[path[5:]]
        elif PLAIN_PATH_RE.fullmatch(path): value = get_path(data, path)
        else:
            try: value = eval_expression(path, data if not isinstance(data, dict) or "vars" in data else {**data, "vars": _vars_var.get()})
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


PINNABLE = {"agent", "sort", "limit", "dedupe", "split", "aggregate", "rename", "datetime", "crypto", "convert", "table", "app", "input", "transform", "template", "http", "delay", "set", "filter", "csv", "notify", "email", "subflow", "code", "sql", "rss", "sheets", "ai", "file"}
# Nodes that support retries and "continue on error".
ERROR_SETTINGS = {"agent", "sort", "limit", "dedupe", "split", "aggregate", "rename", "datetime", "crypto", "convert", "table", "app", "input", "transform", "template", "set", "filter", "csv", "http", "notify", "email", "subflow", "code", "delay", "sql", "rss", "telegram", "github", "sheets", "gmail", "ai", "file"}
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


# ---------------------------------------------------------------------------
# App Action: one node, many services. Each service is a small function over the HTTP helper.
# ---------------------------------------------------------------------------
APP_OPS = {
    "slack": ["post_message", "update_message", "add_reaction", "list_channels"],
    "notion": ["create_page", "query_database", "get_page", "update_page"],
    "airtable": ["list_records", "create_record", "update_record", "get_record", "delete_record"],
    "stripe": ["list_customers", "create_customer", "list_payments", "get_customer", "create_refund", "list_invoices"],
    "twilio": ["send_sms"],
    "linear": ["create_issue"],
    "hubspot": ["create_contact", "list_contacts", "update_contact", "create_deal"],
    "sendgrid": ["send_email"],
    "mailchimp": ["add_subscriber"],
    "trello": ["create_card", "add_comment"],
    "asana": ["create_task"],
    "jira": ["create_issue"],
    "todoist": ["create_task", "list_tasks", "close_task"],
    "gitlab": ["create_issue"],
    "discord": ["post_message"],
    "ntfy": ["publish"],
    "shopify": ["list_orders", "list_products"],
    "zendesk": ["create_ticket"],
    "pipedrive": ["create_person", "create_deal"],
    "clickup": ["create_task"],
    "monday": ["create_item"],
    "dropbox": ["list_folder", "create_folder"],
    "resend": ["send_email"],
    "postmark": ["send_email"],
    "whatsapp": ["send_text"],
    "pushover": ["send"],
    "brevo": ["send_email"],
    "mailgun": ["send_email"],
    "telegram": ["send_message"],
    "github": ["create_issue", "add_comment"],
    "pagerduty": ["trigger_event"],
    "opsgenie": ["create_alert"],
    "intercom": ["create_contact"],
    "freshdesk": ["create_ticket"],
    "supabase": ["insert_row", "select_rows"],
    "bitly": ["shorten"],
    "convertkit": ["add_subscriber"],
    "mattermost": ["post_message"],
    "gotify": ["send"],
    "cloudflare": ["purge_cache"],
    "posthog": ["capture"],
    "mixpanel": ["track"],
    "vonage": ["send_sms"],
    "msteams": ["post_message"],
    "googlechat": ["post_message"],
    "datadog": ["create_event"],
    "segment": ["track"],
    "webex": ["post_message"],
    "rocketchat": ["post_message"],
    "pushbullet": ["push_note"],
    "messagebird": ["send_sms"],
    "mailjet": ["send_email"],
    "loops": ["create_contact"],
    "buttondown": ["add_subscriber"],
    "baserow": ["create_row"],
    "healthchecks": ["ping"],
    "mastodon": ["post_status"],
    "matrix": ["send_message"],
    "telnyx": ["send_sms"],
    "zulip": ["send_message"],
    "ifttt": ["trigger"],
    "uptimekuma": ["push"],
    "gcalendar": ["list_events", "create_event"],
    "gdrive": ["list_files", "create_folder"],
}
OPTIONAL_CREDENTIAL = {"ntfy", "healthchecks", "uptimekuma"}
OAUTH_SERVICES = {"gcalendar", "gdrive"}
NOTION_ID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")
AIRTABLE_BASE_RE = re.compile(r"^app[A-Za-z0-9]{8,30}$")
AIRTABLE_TABLE_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,80}$")
PHONE_RE = re.compile(r"^\+?[0-9]{6,15}$")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")


def _text(f: dict, key: str, label: str, required: bool = True, maxlen: int = 4000) -> str:
    val = f.get(key)
    val = "" if val is None else str(val).strip()
    if required and not val: raise ValueError(f"{label} is required")
    if len(val) > maxlen: raise ValueError(f"{label} is too long (limit {maxlen} characters)")
    return val


def _limit(f: dict, default: int = 20, top: int = 100) -> int:
    try: return max(1, min(int(float(f.get("limit") or default)), top))
    except (TypeError, ValueError): raise ValueError("Limit must be a number")


def _object(f: dict, key: str, label: str, required: bool = False) -> dict:
    val = f.get(key)
    if val is None or val == "":
        if required: raise ValueError(f"{label} is required")
        return {}
    if not isinstance(val, dict): raise ValueError(f"{label} must be a JSON object")
    return val


def app_slack(op: str, secret: str, f: dict) -> Any:
    if not secret.startswith(("xoxb-", "xoxp-")): raise ValueError("The Slack credential must be a bot token starting with xoxb-")
    h = {"Authorization": f"Bearer {secret}"}

    def check(res: Any) -> dict:
        if not isinstance(res, dict) or not res.get("ok"): raise ValueError(f"Slack error: {res.get('error', 'unknown') if isinstance(res, dict) else 'unexpected response'}")
        return res
    if op == "list_channels":
        res = check(do_request("GET", "https://slack.com/api/conversations.list", h, params={"limit": _limit(f, 50, 200), "exclude_archived": "true"}))
        return [{"id": c.get("id"), "name": c.get("name"), "members": c.get("num_members")} for c in res.get("channels") or []]
    channel = _text(f, "channel", "Channel", maxlen=80)
    if not re.fullmatch(r"[#@A-Za-z0-9_.\-]{1,80}", channel): raise ValueError("Channel must look like #general or a channel ID")
    if op in ("update_message", "add_reaction"):
        ts = _text(f, "ts", "Message timestamp", maxlen=30)
        if not re.fullmatch(r"\d{6,12}\.\d{3,8}", ts): raise ValueError("Message timestamp looks like 1700000000.000100 (the ts that Post a message returns)")
        if op == "update_message": res = check(do_request("POST", "https://slack.com/api/chat.update", h, json={"channel": channel, "ts": ts, "text": _text(f, "text", "Text")}))
        else:
            name = _text(f, "name", "Emoji name", maxlen=60).strip(":")
            if not re.fullmatch(r"[a-z0-9_+\-]{1,60}", name): raise ValueError("Emoji name is lowercase, like thumbsup")
            res = check(do_request("POST", "https://slack.com/api/reactions.add", h, json={"channel": channel, "timestamp": ts, "name": name}))
        return {"ok": True, "channel": res.get("channel"), "ts": res.get("ts") or ts}
    res = do_request("POST", "https://slack.com/api/chat.postMessage", {"Authorization": f"Bearer {secret}"}, json={"channel": channel, "text": _text(f, "text", "Text")})
    if not isinstance(res, dict) or not res.get("ok"): raise ValueError(f"Slack error: {res.get('error', 'unknown') if isinstance(res, dict) else 'unexpected response'}")
    return {"ok": True, "channel": res.get("channel"), "ts": res.get("ts")}


def app_notion(op: str, secret: str, f: dict) -> Any:
    headers = {"Authorization": f"Bearer {secret}", "Notion-Version": "2022-06-28"}
    if op in ("get_page", "update_page"):
        page = _text(f, "page_id", "Page ID", maxlen=40)
        if not NOTION_ID_RE.fullmatch(page): raise ValueError("Page ID must be the 32-character ID from the page's URL")
        if op == "get_page": res = do_request("GET", f"https://api.notion.com/v1/pages/{page}", headers)
        else:
            body: dict[str, Any] = {}
            props = _object(f, "properties", "Properties")
            if props: body["properties"] = props
            if str(f.get("archived")).lower() in ("true", "false"): body["archived"] = str(f.get("archived")).lower() == "true"
            if not body: raise ValueError("Give properties to change, or archived: true")
            res = do_request("PATCH", f"https://api.notion.com/v1/pages/{page}", headers, json=body)
        return {"id": res.get("id"), "url": res.get("url"), "archived": res.get("archived"), "properties": res.get("properties")} if isinstance(res, dict) else res
    db = _text(f, "database_id", "Database ID", maxlen=40)
    if not NOTION_ID_RE.fullmatch(db): raise ValueError("Database ID must be the 32-character ID from the database's URL")
    if op == "create_page":
        title_prop = _text(f, "title_property", "Title property", required=False, maxlen=100) or "Name"
        props = {title_prop: {"title": [{"text": {"content": _text(f, "title", "Title", maxlen=2000)}}]}}
        props.update(_object(f, "properties", "Properties"))
        res = do_request("POST", "https://api.notion.com/v1/pages", headers, json={"parent": {"database_id": db}, "properties": props})
        return {"id": res.get("id"), "url": res.get("url")} if isinstance(res, dict) else res
    res = do_request("POST", f"https://api.notion.com/v1/databases/{db}/query", headers, json={"page_size": _limit(f)})
    out = []
    for r in (res.get("results") if isinstance(res, dict) else None) or []:
        title = ""
        for prop in (r.get("properties") or {}).values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                title = "".join(t.get("plain_text", "") for t in prop.get("title") or [])
                break
        out.append({"id": r.get("id"), "url": r.get("url"), "title": title})
    return out


def app_airtable(op: str, secret: str, f: dict) -> Any:
    base_id, table = _text(f, "base_id", "Base ID", maxlen=40), _text(f, "table", "Table", maxlen=80)
    if not AIRTABLE_BASE_RE.fullmatch(base_id): raise ValueError("Base ID must look like appXXXXXXXXXXXXXX")
    if not AIRTABLE_TABLE_RE.fullmatch(table): raise ValueError("Table name has unsupported characters")
    url, headers = f"https://api.airtable.com/v0/{base_id}/{quote(table)}", {"Authorization": f"Bearer {secret}"}
    if op == "list_records":
        res = do_request("GET", f"{url}?maxRecords={_limit(f)}", headers)
        return [{"_id": r.get("id"), **(r.get("fields") or {})} for r in (res.get("records") if isinstance(res, dict) else None) or []]
    if op in ("get_record", "delete_record"):
        rec = _text(f, "record_id", "Record ID", maxlen=30)
        if not re.fullmatch(r"rec[A-Za-z0-9]{10,20}", rec): raise ValueError("Record ID looks like recXXXXXXXXXXXXXX")
        res = do_request("GET" if op == "get_record" else "DELETE", f"{url}/{rec}", headers)
        if op == "delete_record": return {"deleted": bool(res.get("deleted")) if isinstance(res, dict) else True, "id": rec}
        return {"_id": res.get("id"), **(res.get("fields") or {})} if isinstance(res, dict) else res
    if op == "update_record":
        rec = _text(f, "record_id", "Record ID", maxlen=30)
        if not re.fullmatch(r"rec[A-Za-z0-9]{10,20}", rec): raise ValueError("Record ID looks like recXXXXXXXXXXXXXX")
        changes = _object(f, "record", "Record", required=True)
        if not changes: raise ValueError("Record needs at least one field to change")
        res = do_request("PATCH", f"{url}/{rec}", headers, json={"fields": changes})
        return {"_id": res.get("id"), **(res.get("fields") or {})} if isinstance(res, dict) else res
    res = do_request("POST", url, headers, json={"fields": _object(f, "record", "Record", required=True)})
    return {"_id": res.get("id"), **(res.get("fields") or {})} if isinstance(res, dict) else res


def app_stripe(op: str, secret: str, f: dict) -> Any:
    if not secret.startswith(("sk_", "rk_")): raise ValueError("The Stripe credential must be a secret key starting with sk_ or rk_")
    headers = {"Authorization": f"Bearer {secret}"}
    if op == "create_customer":
        form = {k: _text(f, k, k.title(), required=False, maxlen=200) for k in ("email", "name")}
        form = {k: v for k, v in form.items() if v}
        if not form: raise ValueError("Provide an email or a name")
        res = do_request("POST", "https://api.stripe.com/v1/customers", headers, data=form)
        return {"id": res.get("id"), "email": res.get("email"), "name": res.get("name")} if isinstance(res, dict) else res
    if op == "get_customer":
        cid = _text(f, "customer_id", "Customer ID", maxlen=40)
        if not re.fullmatch(r"cus_[A-Za-z0-9]{6,40}", cid): raise ValueError("Customer ID looks like cus_XXXXXXXX")
        res = do_request("GET", f"https://api.stripe.com/v1/customers/{cid}", headers)
        return {k: res.get(k) for k in ("id", "email", "name", "balance", "currency", "created")} if isinstance(res, dict) else res
    if op == "create_refund":
        pi = _text(f, "payment_intent", "Payment intent", maxlen=40)
        if not re.fullmatch(r"pi_[A-Za-z0-9]{6,40}", pi): raise ValueError("Payment intent looks like pi_XXXXXXXX")
        form = {"payment_intent": pi}
        amount = _text(f, "amount", "Amount", required=False, maxlen=12)
        if amount:
            if not amount.isdigit() or int(amount) < 1: raise ValueError("Amount is a whole number in the smallest unit (cents), or leave it empty to refund everything")
            form["amount"] = amount
        res = do_request("POST", "https://api.stripe.com/v1/refunds", headers, data=form)
        return {k: res.get(k) for k in ("id", "amount", "currency", "status")} if isinstance(res, dict) else res
    if op == "list_invoices":
        res = do_request("GET", f"https://api.stripe.com/v1/invoices?limit={_limit(f)}", headers)
        return [{k: r.get(k) for k in ("id", "customer", "amount_due", "currency", "status")} for r in (res.get("data") if isinstance(res, dict) else None) or []]
    path = "customers" if op == "list_customers" else "payment_intents"
    res = do_request("GET", f"https://api.stripe.com/v1/{path}?limit={_limit(f)}", headers)
    rows = (res.get("data") if isinstance(res, dict) else None) or []
    keys = ("id", "email", "name") if op == "list_customers" else ("id", "amount", "currency", "status")
    return [{k: r.get(k) for k in keys} for r in rows]


def app_twilio(op: str, secret: str, f: dict) -> Any:
    m = re.fullmatch(r"(AC[0-9a-fA-F]{32}):([^\s:]{16,64})", secret)
    if not m: raise ValueError("The Twilio credential must look like ACxxxxxxxx:auth_token")
    to, sender = _text(f, "to", "To", maxlen=20), _text(f, "from", "From", maxlen=20)
    if not PHONE_RE.fullmatch(to) or not PHONE_RE.fullmatch(sender): raise ValueError("Phone numbers must look like +15551234567")
    body = _text(f, "body", "Message", maxlen=1600)
    res = do_request("POST", f"https://api.twilio.com/2010-04-01/Accounts/{m.group(1)}/Messages.json", {}, data={"To": to, "From": sender, "Body": body}, auth=(m.group(1), m.group(2)))
    return {"sid": res.get("sid"), "status": res.get("status")} if isinstance(res, dict) else res


def app_linear(op: str, secret: str, f: dict) -> Any:
    team = _text(f, "team_id", "Team ID", maxlen=40)
    if not UUID_RE.fullmatch(team): raise ValueError("Team ID must be the UUID of the Linear team")
    query = "mutation($input: IssueCreateInput!){issueCreate(input:$input){success issue{id identifier url}}}"
    variables = {"input": {"teamId": team, "title": _text(f, "title", "Title", maxlen=250), "description": _text(f, "description", "Description", required=False, maxlen=20000)}}
    res = do_request("POST", "https://api.linear.app/graphql", {"Authorization": secret}, json={"query": query, "variables": variables})
    if not isinstance(res, dict) or res.get("errors"): raise ValueError("Linear error: " + (str((res.get("errors") or [{}])[0].get("message", "unknown")) if isinstance(res, dict) else "unexpected response"))
    return ((res.get("data") or {}).get("issueCreate") or {}).get("issue")


def app_hubspot(op: str, secret: str, f: dict) -> Any:
    headers, url = {"Authorization": f"Bearer {secret}"}, "https://api.hubapi.com/crm/v3/objects/contacts"
    keys = ("email", "firstname", "lastname", "phone", "company")
    if op == "update_contact":
        cid = _text(f, "contact_id", "Contact ID", maxlen=20)
        if not cid.isdigit(): raise ValueError("Contact ID is a number")
        props = {k: _text(f, k, k, required=False, maxlen=200) for k in keys}
        props = {k: v for k, v in props.items() if v}
        if not props: raise ValueError("Give at least one property to change")
        res = do_request("PATCH", f"{url}/{cid}", headers, json={"properties": props})
        return {"id": res.get("id"), **(res.get("properties") or {})} if isinstance(res, dict) else res
    if op == "create_deal":
        props = {"dealname": _text(f, "dealname", "Deal name", maxlen=200)}
        for k in ("amount", "dealstage", "pipeline"):
            v = _text(f, k, k, required=False, maxlen=100)
            if v: props[k] = v
        if "amount" in props and not re.fullmatch(r"\d+(\.\d+)?", props["amount"]): raise ValueError("Amount is a number")
        res = do_request("POST", "https://api.hubapi.com/crm/v3/objects/deals", headers, json={"properties": props})
        return {"id": res.get("id"), **(res.get("properties") or {})} if isinstance(res, dict) else res
    if op == "create_contact":
        props = {k: _text(f, k, k, required=False, maxlen=200) for k in keys}
        props = {k: v for k, v in props.items() if v}
        if not props.get("email"): raise ValueError("Email is required")
        res = do_request("POST", url, headers, json={"properties": props})
        return {"id": res.get("id"), **(res.get("properties") or {})} if isinstance(res, dict) else res
    res = do_request("GET", f"{url}?limit={_limit(f)}&properties={','.join(keys)}", headers)
    return [{"id": r.get("id"), **{k: (r.get("properties") or {}).get(k) for k in keys}} for r in (res.get("results") if isinstance(res, dict) else None) or []]


def app_sendgrid(op: str, secret: str, f: dict) -> Any:
    to, sender = _text(f, "to", "To", maxlen=200), _text(f, "from", "From", maxlen=200)
    for addr in (to, sender):
        if not EMAIL_RE.fullmatch(addr): raise ValueError(f"Invalid email address: {addr!r}")
    subject = _text(f, "subject", "Subject", maxlen=200)
    if "\n" in subject or "\r" in subject: raise ValueError("Subject must be one line")
    body = {"personalizations": [{"to": [{"email": to}]}], "from": {"email": sender}, "subject": subject, "content": [{"type": "text/plain", "value": _text(f, "text", "Text", maxlen=100_000)}]}
    do_request("POST", "https://api.sendgrid.com/v3/mail/send", {"Authorization": f"Bearer {secret}"}, json=body)
    return {"sent": True, "to": to}


def app_mailchimp(op: str, secret: str, f: dict) -> Any:
    m = re.fullmatch(r"[0-9a-fA-F]{20,40}-([a-z]{2}\d{1,3})", secret)
    if not m: raise ValueError("The Mailchimp credential must be an API key ending in a data centre such as -us21")
    list_id = _text(f, "list_id", "Audience ID", maxlen=20)
    if not re.fullmatch(r"[A-Za-z0-9]{6,20}", list_id): raise ValueError("Audience ID has unsupported characters")
    email = _text(f, "email", "Email", maxlen=200)
    if not EMAIL_RE.fullmatch(email): raise ValueError(f"Invalid email address: {email!r}")
    status = _text(f, "status", "Status", required=False, maxlen=20) or "subscribed"
    if status not in ("subscribed", "pending", "unsubscribed"): raise ValueError("Status must be subscribed, pending or unsubscribed")
    merge = {k: v for k, v in (("FNAME", _text(f, "first_name", "First name", required=False, maxlen=100)), ("LNAME", _text(f, "last_name", "Last name", required=False, maxlen=100))) if v}
    body: dict[str, Any] = {"email_address": email, "status": status}
    if merge: body["merge_fields"] = merge
    res = do_request("POST", f"https://{m.group(1)}.api.mailchimp.com/3.0/lists/{list_id}/members", {}, json=body, auth=("flowpilot", secret))
    return {"id": res.get("id"), "email": res.get("email_address"), "status": res.get("status")} if isinstance(res, dict) else res


def app_trello(op: str, secret: str, f: dict) -> Any:
    key, _, tok = secret.partition(":")
    if not key or not tok: raise ValueError("The Trello credential must look like API_KEY:TOKEN")
    if op == "add_comment":
        card = _text(f, "card_id", "Card ID", maxlen=24)
        if not re.fullmatch(r"[0-9a-f]{24}", card): raise ValueError("Card ID must be the 24-character ID of a Trello card")
        res = do_request("POST", f"https://api.trello.com/1/cards/{card}/actions/comments", {}, params={"key": key, "token": tok, "text": _text(f, "text", "Comment", maxlen=16384)})
        return {"id": res.get("id")} if isinstance(res, dict) else res
    list_id = _text(f, "list_id", "List ID", maxlen=24)
    if not re.fullmatch(r"[0-9a-f]{24}", list_id): raise ValueError("List ID must be the 24-character ID of a Trello list")
    res = do_request("POST", "https://api.trello.com/1/cards", {}, params={"key": key, "token": tok, "idList": list_id, "name": _text(f, "name", "Card name", maxlen=500), "desc": _text(f, "description", "Description", required=False, maxlen=16000)})
    return {"id": res.get("id"), "url": res.get("shortUrl")} if isinstance(res, dict) else res


def app_asana(op: str, secret: str, f: dict) -> Any:
    data: dict[str, Any] = {"name": _text(f, "name", "Task name", maxlen=500), "notes": _text(f, "notes", "Notes", required=False, maxlen=20000)}
    project = _text(f, "project_id", "Project ID", required=False, maxlen=30)
    if project:
        if not project.isdigit(): raise ValueError("Project ID must be a number")
        data["projects"] = [project]
    res = do_request("POST", "https://app.asana.com/api/1.0/tasks", {"Authorization": f"Bearer {secret}"}, json={"data": data})
    task = res.get("data") if isinstance(res, dict) else None
    return {"id": (task or {}).get("gid"), "url": (task or {}).get("permalink_url")}


def app_jira(op: str, secret: str, f: dict) -> Any:
    email, _, token = secret.partition(":")
    if not EMAIL_RE.fullmatch(email) or not token: raise ValueError("The Jira credential must look like you@example.com:API_TOKEN")
    site = _text(f, "site", "Site", maxlen=63)
    if not re.fullmatch(r"[a-z0-9-]{1,63}", site): raise ValueError("Site is the part before .atlassian.net, for example mycompany")
    project = _text(f, "project", "Project key", maxlen=20)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,19}", project): raise ValueError("Project key looks like ENG")
    fields: dict[str, Any] = {"project": {"key": project}, "summary": _text(f, "summary", "Summary", maxlen=250), "issuetype": {"name": _text(f, "type", "Issue type", required=False, maxlen=40) or "Task"}}
    desc = _text(f, "description", "Description", required=False, maxlen=30000)
    if desc: fields["description"] = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": desc}]}]}
    res = do_request("POST", f"https://{site}.atlassian.net/rest/api/3/issue", {}, json={"fields": fields}, auth=(email, token))
    return {"key": res.get("key"), "id": res.get("id"), "url": f"https://{site}.atlassian.net/browse/{res.get('key')}"} if isinstance(res, dict) else res


def app_todoist(op: str, secret: str, f: dict) -> Any:
    if op == "list_tasks":
        project = _text(f, "project_id", "Project ID", required=False, maxlen=30)
        if project and not re.fullmatch(r"[A-Za-z0-9]{1,30}", project): raise ValueError("Project ID has unsupported characters")
        res = do_request("GET", "https://api.todoist.com/rest/v2/tasks", {"Authorization": f"Bearer {secret}"}, params={"project_id": project} if project else {})
        return [{"id": x.get("id"), "content": x.get("content"), "due": (x.get("due") or {}).get("string"), "url": x.get("url")} for x in (res if isinstance(res, list) else [])][:_limit(f, 50, 200)]
    if op == "close_task":
        task = _text(f, "task_id", "Task ID", maxlen=30)
        if not re.fullmatch(r"[A-Za-z0-9]{1,30}", task): raise ValueError("Task ID has unsupported characters")
        do_request("POST", f"https://api.todoist.com/rest/v2/tasks/{task}/close", {"Authorization": f"Bearer {secret}"})
        return {"closed": True, "id": task}
    body = {"content": _text(f, "content", "Task", maxlen=500), "description": _text(f, "description", "Description", required=False, maxlen=16000)}
    due = _text(f, "due", "Due", required=False, maxlen=60)
    if due: body["due_string"] = due
    res = do_request("POST", "https://api.todoist.com/rest/v2/tasks", {"Authorization": f"Bearer {secret}"}, json=body)
    return {"id": res.get("id"), "url": res.get("url")} if isinstance(res, dict) else res


def app_gitlab(op: str, secret: str, f: dict) -> Any:
    project = _text(f, "project", "Project", maxlen=200)
    if not re.fullmatch(r"\d{1,12}|[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+){1,10}", project) or any(part.strip(".") == "" for part in project.split("/")): raise ValueError("Project is a number or a path like group/name")
    res = do_request("POST", f"https://gitlab.com/api/v4/projects/{quote(project, safe='')}/issues", {"PRIVATE-TOKEN": secret}, json={"title": _text(f, "title", "Title", maxlen=250), "description": _text(f, "description", "Description", required=False, maxlen=100_000)})
    return {"iid": res.get("iid"), "url": res.get("web_url")} if isinstance(res, dict) else res


def app_discord(op: str, secret: str, f: dict) -> Any:
    channel = _text(f, "channel_id", "Channel ID", maxlen=25)
    if not channel.isdigit(): raise ValueError("Channel ID is a long number (turn on Developer Mode in Discord, then Copy Channel ID)")
    res = do_request("POST", f"https://discord.com/api/v10/channels/{channel}/messages", {"Authorization": f"Bot {secret}"}, json={"content": _text(f, "content", "Message", maxlen=2000)})
    return {"id": res.get("id")} if isinstance(res, dict) else res


def app_ntfy(op: str, secret: str, f: dict) -> Any:
    topic = _text(f, "topic", "Topic", maxlen=64)
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", topic): raise ValueError("Topic uses letters, numbers, - and _ only")
    server = (_text(f, "server", "Server", required=False, maxlen=200) or "https://ntfy.sh").rstrip("/")
    headers: dict[str, str] = {}
    title = _text(f, "title", "Title", required=False, maxlen=200)
    if title:
        if "\n" in title or "\r" in title: raise ValueError("Title must be one line")
        headers["Title"] = title.encode("ascii", "ignore").decode() or "FlowPilot"
    priority = _text(f, "priority", "Priority", required=False, maxlen=1)
    if priority:
        if priority not in "12345": raise ValueError("Priority is a number from 1 to 5")
        headers["Priority"] = priority
    if secret: headers["Authorization"] = f"Bearer {secret}"
    do_request("POST", f"{safe_url(server)}/{topic}", headers, content=_text(f, "message", "Message", maxlen=4000).encode("utf-8"))
    return {"sent": True, "topic": topic}


def _rfc3339(value: str, label: str) -> str:
    try: return _to_datetime(value).isoformat()
    except ValueError as exc: raise ValueError(f"{label}: {exc}")


def app_gcalendar(op: str, token: str, f: dict) -> Any:
    cal = _text(f, "calendar_id", "Calendar ID", required=False, maxlen=200) or "primary"
    if not re.fullmatch(r"primary|[A-Za-z0-9_.@\-]{3,200}", cal): raise ValueError("Calendar ID is primary or an address like name@group.calendar.google.com")
    url, headers = f"https://www.googleapis.com/calendar/v3/calendars/{quote(cal, safe='')}/events", {"Authorization": f"Bearer {token}"}
    if op == "list_events":
        start = _rfc3339(_text(f, "time_min", "Start", required=False, maxlen=40) or datetime.now(timezone.utc).isoformat(), "Start")
        res = do_request("GET", url, headers, params={"maxResults": _limit(f, 20, 250), "singleEvents": "true", "orderBy": "startTime", "timeMin": start})
        out = []
        for e in (res.get("items") if isinstance(res, dict) else None) or []:
            st, en = e.get("start") or {}, e.get("end") or {}
            out.append({"id": e.get("id"), "summary": e.get("summary"), "start": st.get("dateTime") or st.get("date"), "end": en.get("dateTime") or en.get("date"), "location": e.get("location"), "link": e.get("htmlLink")})
        return out
    def when(key: str, label: str) -> dict:
        raw = _text(f, key, label, maxlen=40)
        return {"date": raw} if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) else {"dateTime": _rfc3339(raw, label)}
    body = {"summary": _text(f, "summary", "Title", maxlen=500), "start": when("start", "Start"), "end": when("end", "End")}
    for key in ("description", "location"):
        val = _text(f, key, key.title(), required=False, maxlen=8000)
        if val: body[key] = val
    res = do_request("POST", url, headers, json=body)
    return {"id": res.get("id"), "link": res.get("htmlLink"), "summary": res.get("summary")} if isinstance(res, dict) else res


def app_gdrive(op: str, token: str, f: dict) -> Any:
    headers, url = {"Authorization": f"Bearer {token}"}, "https://www.googleapis.com/drive/v3/files"
    parent = _text(f, "folder_id", "Folder ID", required=False, maxlen=80)
    if parent and not re.fullmatch(r"[A-Za-z0-9_\-]{10,80}", parent): raise ValueError("Folder ID has unsupported characters")
    if op == "create_folder":
        body: dict[str, Any] = {"name": _text(f, "name", "Folder name", maxlen=200), "mimeType": "application/vnd.google-apps.folder"}
        if parent: body["parents"] = [parent]
        res = do_request("POST", url, headers, json=body, params={"fields": "id,name,webViewLink"})
        return {"id": res.get("id"), "name": res.get("name"), "url": res.get("webViewLink")} if isinstance(res, dict) else res
    clauses = ["trashed = false"]
    needle = _text(f, "name_contains", "Name contains", required=False, maxlen=200)
    if needle: clauses.append("name contains '" + needle.replace("\\", "\\\\").replace("'", "\\'") + "'")
    if parent: clauses.append(f"'{parent}' in parents")
    res = do_request("GET", url, headers, params={"q": " and ".join(clauses), "pageSize": _limit(f, 20, 100), "orderBy": "modifiedTime desc", "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size)"})
    return [{"id": x.get("id"), "name": x.get("name"), "type": x.get("mimeType"), "modified": x.get("modifiedTime"), "url": x.get("webViewLink"), "size": x.get("size")} for x in (res.get("files") if isinstance(res, dict) else None) or []]


SUBDOMAIN_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


def _subdomain(f: dict, key: str, label: str) -> str:
    val = _text(f, key, label, maxlen=63).lower()
    if not SUBDOMAIN_RE.fullmatch(val): raise ValueError(f"{label} is the part before the first dot, for example mystore")
    return val


def app_shopify(op: str, secret: str, f: dict) -> Any:
    shop = _subdomain(f, "shop", "Shop")
    url = f"https://{shop}.myshopify.com/admin/api/2024-10/" + ("orders.json" if op == "list_orders" else "products.json")
    params: dict[str, Any] = {"limit": _limit(f, 20, 50)}
    if op == "list_orders":
        status = _text(f, "status", "Status", required=False, maxlen=20) or "any"
        if status not in ("any", "open", "closed", "cancelled"): raise ValueError("Status must be any, open, closed or cancelled")
        params["status"] = status
    res = do_request("GET", url, {"X-Shopify-Access-Token": secret}, params=params)
    if op == "list_orders":
        return [{k: o.get(k) for k in ("id", "name", "email", "total_price", "currency", "financial_status", "created_at")} for o in (res.get("orders") if isinstance(res, dict) else None) or []]
    return [{k: x.get(k) for k in ("id", "title", "status", "vendor")} for x in (res.get("products") if isinstance(res, dict) else None) or []]


def app_zendesk(op: str, secret: str, f: dict) -> Any:
    email, _, token = secret.partition(":")
    if not EMAIL_RE.fullmatch(email) or not token: raise ValueError("The Zendesk credential must look like you@example.com:API_TOKEN")
    sub = _subdomain(f, "subdomain", "Subdomain")
    ticket: dict[str, Any] = {"subject": _text(f, "subject", "Subject", maxlen=250), "comment": {"body": _text(f, "body", "Body", maxlen=30000)}}
    who = _text(f, "requester_email", "Requester email", required=False, maxlen=200)
    if who:
        if not EMAIL_RE.fullmatch(who): raise ValueError(f"Invalid email address: {who!r}")
        ticket["requester"] = {"email": who}
    res = do_request("POST", f"https://{sub}.zendesk.com/api/v2/tickets.json", {}, json={"ticket": ticket}, auth=(f"{email}/token", token))
    t = (res.get("ticket") if isinstance(res, dict) else None) or {}
    return {"id": t.get("id"), "url": f"https://{sub}.zendesk.com/agent/tickets/{t.get('id')}"}


def app_pipedrive(op: str, secret: str, f: dict) -> Any:
    headers = {"x-api-token": secret}
    if op == "create_person":
        body: dict[str, Any] = {"name": _text(f, "name", "Name", maxlen=200)}
        for key in ("email", "phone"):
            val = _text(f, key, key.title(), required=False, maxlen=200)
            if val: body[key] = [val]
        res = do_request("POST", "https://api.pipedrive.com/v1/persons", headers, json=body)
    else:
        body = {"title": _text(f, "title", "Title", maxlen=250)}
        for key in ("value", "person_id", "org_id"):
            val = _text(f, key, key, required=False, maxlen=30)
            if val:
                if not re.fullmatch(r"\d+(\.\d+)?", val): raise ValueError(f"{key} must be a number")
                body[key] = float(val) if key == "value" else int(float(val))
        cur = _text(f, "currency", "Currency", required=False, maxlen=3)
        if cur: body["currency"] = cur.upper()
        res = do_request("POST", "https://api.pipedrive.com/v1/deals", headers, json=body)
    data = (res.get("data") if isinstance(res, dict) else None) or {}
    return {"id": data.get("id"), "name": data.get("name") or data.get("title")}


def app_clickup(op: str, secret: str, f: dict) -> Any:
    list_id = _text(f, "list_id", "List ID", maxlen=20)
    if not list_id.isdigit(): raise ValueError("List ID is a number (open the list and copy it from the address)")
    body: dict[str, Any] = {"name": _text(f, "name", "Task name", maxlen=500), "description": _text(f, "description", "Description", required=False, maxlen=20000)}
    prio = _text(f, "priority", "Priority", required=False, maxlen=1)
    if prio:
        if prio not in "1234": raise ValueError("Priority is 1 (urgent) to 4 (low)")
        body["priority"] = int(prio)
    res = do_request("POST", f"https://api.clickup.com/api/v2/list/{list_id}/task", {"Authorization": secret}, json=body)
    return {"id": res.get("id"), "url": res.get("url")} if isinstance(res, dict) else res


def app_monday(op: str, secret: str, f: dict) -> Any:
    board = _text(f, "board_id", "Board ID", maxlen=20)
    if not board.isdigit(): raise ValueError("Board ID is a number")
    cols = _object(f, "column_values", "Column values")
    query = "mutation($board: ID!, $name: String!, $cols: JSON){create_item(board_id: $board, item_name: $name, column_values: $cols){id}}"
    res = do_request("POST", "https://api.monday.com/v2", {"Authorization": secret}, json={"query": query, "variables": {"board": board, "name": _text(f, "item_name", "Item name", maxlen=255), "cols": json.dumps(cols)}})
    if not isinstance(res, dict) or res.get("errors"): raise ValueError("monday.com error: " + (str((res.get("errors") or [{}])[0].get("message", "unknown")) if isinstance(res, dict) else "unexpected response"))
    return {"id": ((res.get("data") or {}).get("create_item") or {}).get("id")}


def _dropbox_path(f: dict, key: str, allow_root: bool) -> str:
    val = _text(f, key, "Path", required=not allow_root, maxlen=300)
    if val and (not val.startswith("/") or any(p in ("..", ".") for p in val.split("/")) or "\x00" in val): raise ValueError("Path starts with / and cannot contain .. (for example /Reports)")
    return val


def app_dropbox(op: str, secret: str, f: dict) -> Any:
    headers = {"Authorization": f"Bearer {secret}"}
    if op == "create_folder":
        res = do_request("POST", "https://api.dropboxapi.com/2/files/create_folder_v2", headers, json={"path": _dropbox_path(f, "path", False), "autorename": False})
        return {"path": ((res.get("metadata") if isinstance(res, dict) else None) or {}).get("path_display")}
    res = do_request("POST", "https://api.dropboxapi.com/2/files/list_folder", headers, json={"path": _dropbox_path(f, "path", True), "limit": _limit(f, 50, 200)})
    return [{"name": e.get("name"), "type": e.get(".tag"), "path": e.get("path_display"), "size": e.get("size")} for e in (res.get("entries") if isinstance(res, dict) else None) or []]


def _mail_fields(f: dict) -> tuple[str, str, str, str]:
    to, sender = _text(f, "to", "To", maxlen=200), _text(f, "from", "From", maxlen=200)
    for addr in (to, sender.split("<")[-1].rstrip(">").strip()):
        if not EMAIL_RE.fullmatch(addr): raise ValueError(f"Invalid email address: {addr!r}")
    subject = _text(f, "subject", "Subject", maxlen=200)
    if "\n" in subject or "\r" in subject: raise ValueError("Subject must be one line")
    return to, sender, subject, _text(f, "text", "Text", maxlen=100_000)


def app_resend(op: str, secret: str, f: dict) -> Any:
    to, sender, subject, text = _mail_fields(f)
    res = do_request("POST", "https://api.resend.com/emails", {"Authorization": f"Bearer {secret}"}, json={"from": sender, "to": [to], "subject": subject, "text": text})
    return {"id": res.get("id")} if isinstance(res, dict) else res


def app_postmark(op: str, secret: str, f: dict) -> Any:
    to, sender, subject, text = _mail_fields(f)
    res = do_request("POST", "https://api.postmarkapp.com/email", {"X-Postmark-Server-Token": secret, "Accept": "application/json"}, json={"From": sender, "To": to, "Subject": subject, "TextBody": text, "MessageStream": "outbound"})
    return {"message_id": res.get("MessageID")} if isinstance(res, dict) else res


def app_whatsapp(op: str, secret: str, f: dict) -> Any:
    phone_id, to = _text(f, "phone_number_id", "Phone number ID", maxlen=30), _text(f, "to", "To", maxlen=20).lstrip("+")
    if not phone_id.isdigit(): raise ValueError("Phone number ID is a number from the WhatsApp Cloud API setup page")
    if not re.fullmatch(r"\d{6,15}", to): raise ValueError("To is a phone number with country code, for example 15551230000")
    res = do_request("POST", f"https://graph.facebook.com/v19.0/{phone_id}/messages", {"Authorization": f"Bearer {secret}"}, json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": _text(f, "text", "Message", maxlen=4000)}})
    msgs = (res.get("messages") if isinstance(res, dict) else None) or [{}]
    return {"message_id": msgs[0].get("id")}


def app_pushover(op: str, secret: str, f: dict) -> Any:
    token, _, user = secret.partition(":")
    if not token or not user: raise ValueError("The Pushover credential must look like APP_TOKEN:USER_KEY")
    form = {"token": token, "user": user, "message": _text(f, "message", "Message", maxlen=1024)}
    title = _text(f, "title", "Title", required=False, maxlen=250)
    if title: form["title"] = title
    prio = _text(f, "priority", "Priority", required=False, maxlen=2)
    if prio:
        if prio not in ("-2", "-1", "0", "1"): raise ValueError("Priority is -2, -1, 0 or 1")
        form["priority"] = prio
    res = do_request("POST", "https://api.pushover.net/1/messages.json", {}, data=form)
    return {"status": res.get("status"), "request": res.get("request")} if isinstance(res, dict) else res


def app_brevo(op: str, secret: str, f: dict) -> Any:
    to, sender, subject, text = _mail_fields(f)
    name, _, rest = sender.partition("<")
    email = rest.rstrip(">").strip() if rest else sender
    res = do_request("POST", "https://api.brevo.com/v3/smtp/email", {"api-key": secret, "Accept": "application/json"}, json={"sender": {"email": email, **({"name": name.strip()} if name.strip() else {})}, "to": [{"email": to}], "subject": subject, "textContent": text})
    return {"message_id": res.get("messageId")} if isinstance(res, dict) else res


def app_mailgun(op: str, secret: str, f: dict) -> Any:
    to, sender, subject, text = _mail_fields(f)
    domain = _text(f, "domain", "Domain", maxlen=100)
    if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?)+", domain): raise ValueError("Domain is your sending domain, like mg.example.com")
    region = _text(f, "region", "Region", required=False, maxlen=2).lower() or "us"
    if region not in ("us", "eu"): raise ValueError("Region is us or eu")
    host = "api.eu.mailgun.net" if region == "eu" else "api.mailgun.net"
    auth = base64.b64encode(f"api:{secret}".encode()).decode()
    res = do_request("POST", f"https://{host}/v3/{domain}/messages", {"Authorization": f"Basic {auth}"}, data={"from": sender, "to": to, "subject": subject, "text": text})
    return {"id": res.get("id"), "message": res.get("message")} if isinstance(res, dict) else res


def app_telegram(op: str, secret: str, f: dict) -> Any:
    if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_\-]{20,60}", secret): raise ValueError("The Telegram credential is the bot token from @BotFather (like 123456:ABC...)")
    chat = _text(f, "chat_id", "Chat ID", maxlen=60)
    if not re.fullmatch(r"-?\d{1,20}|@[A-Za-z0-9_]{4,60}", chat): raise ValueError("Chat ID is a number (or @channelname for public channels)")
    res = do_request("POST", f"https://api.telegram.org/bot{secret}/sendMessage", {}, json={"chat_id": chat, "text": _text(f, "text", "Message", maxlen=4000)})
    msg = (res.get("result") if isinstance(res, dict) else None) or {}
    return {"message_id": msg.get("message_id")}


def app_github(op: str, secret: str, f: dict) -> Any:
    repo = _text(f, "repo", "Repository", maxlen=140)
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,100}/[A-Za-z0-9_.\-]{1,100}", repo) or any(part.strip(".") == "" for part in repo.split("/")): raise ValueError("Repository looks like owner/name")
    headers = {"Authorization": f"Bearer {secret}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if op == "add_comment":
        num = _text(f, "number", "Issue number", maxlen=10)
        if not num.isdigit() or int(num) < 1: raise ValueError("Issue number is a positive number")
        res = do_request("POST", f"https://api.github.com/repos/{repo}/issues/{int(num)}/comments", headers, json={"body": _text(f, "body", "Comment", maxlen=60_000)})
        return {"id": res.get("id"), "url": res.get("html_url")} if isinstance(res, dict) else res
    body: dict[str, Any] = {"title": _text(f, "title", "Title", maxlen=250), "body": _text(f, "body", "Description", required=False, maxlen=60_000)}
    labels = f.get("labels")
    if labels:
        if not isinstance(labels, list) or len(labels) > 10 or not all(isinstance(x, str) and 0 < len(x) <= 50 for x in labels): raise ValueError("Labels is a list of up to 10 short names")
        body["labels"] = labels
    res = do_request("POST", f"https://api.github.com/repos/{repo}/issues", headers, json=body)
    return {"number": res.get("number"), "url": res.get("html_url")} if isinstance(res, dict) else res


def _email(f: dict, key: str = "email", required: bool = True) -> str:
    addr = _text(f, key, "Email", required=required, maxlen=200)
    if addr and not EMAIL_RE.fullmatch(addr): raise ValueError(f"Invalid email address: {addr!r}")
    return addr


def app_pagerduty(op: str, secret: str, f: dict) -> Any:
    severity = _text(f, "severity", "Severity", required=False, maxlen=10).lower() or "error"
    if severity not in ("critical", "error", "warning", "info"): raise ValueError("Severity is critical, error, warning or info")
    body: dict[str, Any] = {"routing_key": secret, "event_action": "trigger", "payload": {"summary": _text(f, "summary", "Summary", maxlen=1024), "source": _text(f, "source", "Source", required=False, maxlen=255) or "flowpilot", "severity": severity}}
    key = _text(f, "dedup_key", "Dedup key", required=False, maxlen=255)
    if key: body["dedup_key"] = key
    res = do_request("POST", "https://events.pagerduty.com/v2/enqueue", {}, json=body)
    return {"status": res.get("status"), "dedup_key": res.get("dedup_key")} if isinstance(res, dict) else res


def app_opsgenie(op: str, secret: str, f: dict) -> Any:
    region = _text(f, "region", "Region", required=False, maxlen=2).lower() or "us"
    if region not in ("us", "eu"): raise ValueError("Region is us or eu")
    body = {"message": _text(f, "message", "Message", maxlen=130), "description": _text(f, "description", "Description", required=False, maxlen=15000)}
    prio = _text(f, "priority", "Priority", required=False, maxlen=2).upper()
    if prio:
        if prio not in ("P1", "P2", "P3", "P4", "P5"): raise ValueError("Priority is P1 to P5")
        body["priority"] = prio
    res = do_request("POST", f"https://api.{'eu.' if region == 'eu' else ''}opsgenie.com/v2/alerts", {"Authorization": f"GenieKey {secret}"}, json=body)
    return {"request_id": res.get("requestId"), "result": res.get("result")} if isinstance(res, dict) else res


def app_intercom(op: str, secret: str, f: dict) -> Any:
    body: dict[str, Any] = {"role": "user", "email": _email(f)}
    for key, label in (("name", "Name"), ("phone", "Phone")):
        val = _text(f, key, label, required=False, maxlen=100)
        if val: body[key] = val
    res = do_request("POST", "https://api.intercom.io/contacts", {"Authorization": f"Bearer {secret}", "Accept": "application/json"}, json=body)
    return {"id": res.get("id"), "email": res.get("email")} if isinstance(res, dict) else res


def app_freshdesk(op: str, secret: str, f: dict) -> Any:
    sub = _subdomain(f, "subdomain", "Subdomain")
    prio = _text(f, "priority", "Priority", required=False, maxlen=1) or "1"
    if prio not in "1234": raise ValueError("Priority is 1 (low) to 4 (urgent)")
    body = {"subject": _text(f, "subject", "Subject", maxlen=250), "description": _text(f, "description", "Description", maxlen=100_000), "email": _email(f), "priority": int(prio), "status": 2}
    auth = base64.b64encode(f"{secret}:X".encode()).decode()
    res = do_request("POST", f"https://{sub}.freshdesk.com/api/v2/tickets", {"Authorization": f"Basic {auth}"}, json=body)
    return {"id": res.get("id"), "url": f"https://{sub}.freshdesk.com/a/tickets/{res.get('id')}"} if isinstance(res, dict) else res


def app_supabase(op: str, secret: str, f: dict) -> Any:
    ref = _text(f, "project", "Project", maxlen=40)
    if not re.fullmatch(r"[a-z0-9]{6,40}", ref): raise ValueError("Project is the short id in your Supabase address (abcdefgh.supabase.co)")
    table = _text(f, "table", "Table", maxlen=63)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", table): raise ValueError("Table uses letters, numbers and _ only")
    headers = {"apikey": secret, "Authorization": f"Bearer {secret}"}
    url = f"https://{ref}.supabase.co/rest/v1/{table}"
    if op == "insert_row":
        row = f.get("row")
        if not isinstance(row, dict) or not row: raise ValueError("Row must be an object of column values")
        res = do_request("POST", url, {**headers, "Prefer": "return=representation"}, json=row)
        return res if isinstance(res, list) else [res]
    res = do_request("GET", url, headers, params={"select": "*", "limit": _limit(f, 50, 200)})
    return res if isinstance(res, list) else []


def app_bitly(op: str, secret: str, f: dict) -> Any:
    link = _text(f, "url", "URL", maxlen=2000)
    if not re.match(r"^https?://[^\s]+$", link): raise ValueError("URL must start with http:// or https://")
    res = do_request("POST", "https://api-ssl.bit.ly/v4/shorten", {"Authorization": f"Bearer {secret}"}, json={"long_url": link})
    return {"link": res.get("link"), "id": res.get("id")} if isinstance(res, dict) else res


def app_convertkit(op: str, secret: str, f: dict) -> Any:
    form = _text(f, "form_id", "Form ID", maxlen=15)
    if not form.isdigit(): raise ValueError("Form ID is a number from the form's address")
    body = {"api_key": secret, "email": _email(f)}
    first = _text(f, "first_name", "First name", required=False, maxlen=100)
    if first: body["first_name"] = first
    res = do_request("POST", f"https://api.convertkit.com/v3/forms/{form}/subscribe", {}, json=body)
    sub = (res.get("subscription") if isinstance(res, dict) else None) or {}
    return {"id": sub.get("id"), "state": sub.get("state")}


def _origin(f: dict, label: str = "Server") -> str:
    """The https origin of a self-hosted service the user names, checked like any other outgoing address."""
    server = _text(f, "server", label, maxlen=200)
    parsed = urlparse(safe_url(server))
    if parsed.path.strip("/") or parsed.query or parsed.fragment: raise ValueError(f"{label} is the address only, like https://chat.example.com")
    return f"https://{parsed.hostname}"


def app_mattermost(op: str, secret: str, f: dict) -> Any:
    channel = _text(f, "channel_id", "Channel ID", maxlen=26)
    if not re.fullmatch(r"[a-z0-9]{26}", channel): raise ValueError("Channel ID is 26 letters and numbers (channel menu, View Info)")
    res = do_request("POST", f"{_origin(f)}/api/v4/posts", {"Authorization": f"Bearer {secret}"}, json={"channel_id": channel, "message": _text(f, "message", "Message", maxlen=16000)})
    return {"id": res.get("id")} if isinstance(res, dict) else res


def app_gotify(op: str, secret: str, f: dict) -> Any:
    body: dict[str, Any] = {"message": _text(f, "message", "Message", maxlen=4000)}
    title = _text(f, "title", "Title", required=False, maxlen=200)
    if title: body["title"] = title
    prio = _text(f, "priority", "Priority", required=False, maxlen=2)
    if prio:
        if not prio.isdigit() or int(prio) > 10: raise ValueError("Priority is a number from 0 to 10")
        body["priority"] = int(prio)
    res = do_request("POST", f"{_origin(f)}/message", {"X-Gotify-Key": secret}, json=body)
    return {"id": res.get("id")} if isinstance(res, dict) else res


def app_cloudflare(op: str, secret: str, f: dict) -> Any:
    zone = _text(f, "zone_id", "Zone ID", maxlen=32)
    if not re.fullmatch(r"[0-9a-f]{32}", zone): raise ValueError("Zone ID is 32 letters and numbers from the zone's overview page")
    files = f.get("files")
    if files:
        if not isinstance(files, list) or len(files) > 30 or not all(isinstance(x, str) and re.match(r"^https?://\S+$", x) for x in files): raise ValueError("Files is a list of up to 30 full addresses")
        body: dict[str, Any] = {"files": files}
    else: body = {"purge_everything": True}
    res = do_request("POST", f"https://api.cloudflare.com/client/v4/zones/{zone}/purge_cache", {"Authorization": f"Bearer {secret}"}, json=body)
    return {"success": res.get("success")} if isinstance(res, dict) else res


def _event_name(f: dict, key: str = "event") -> str:
    return _text(f, key, "Event", maxlen=200)


def app_posthog(op: str, secret: str, f: dict) -> Any:
    region = _text(f, "region", "Region", required=False, maxlen=2).lower() or "us"
    if region not in ("us", "eu"): raise ValueError("Region is us or eu")
    res = do_request("POST", f"https://{region}.i.posthog.com/capture/", {}, json={"api_key": secret, "event": _event_name(f), "distinct_id": _text(f, "distinct_id", "Distinct ID", maxlen=200), "properties": _object(f, "properties", "Properties")})
    return {"status": res.get("status")} if isinstance(res, dict) else res


def app_mixpanel(op: str, secret: str, f: dict) -> Any:
    props = {**_object(f, "properties", "Properties"), "token": secret, "distinct_id": _text(f, "distinct_id", "Distinct ID", maxlen=200)}
    res = do_request("POST", "https://api.mixpanel.com/track", {"Accept": "application/json"}, params={"ip": 0}, json=[{"event": _event_name(f), "properties": props}])
    return {"status": res.get("status")} if isinstance(res, dict) else {"status": res}


def app_vonage(op: str, secret: str, f: dict) -> Any:
    key, _, sec = secret.partition(":")
    if not key or not sec: raise ValueError("The Vonage credential must look like API_KEY:API_SECRET")
    to, sender = _text(f, "to", "To", maxlen=20).lstrip("+"), _text(f, "from", "From", maxlen=11)
    if not re.fullmatch(r"\d{6,15}", to): raise ValueError("To is a phone number with country code, for example 15551230000")
    if not re.fullmatch(r"[A-Za-z0-9]{1,11}", sender): raise ValueError("From is a number or a short name (letters and numbers, up to 11)")
    res = do_request("POST", "https://rest.nexmo.com/sms/json", {}, data={"api_key": key, "api_secret": sec, "to": to, "from": sender, "text": _text(f, "text", "Message", maxlen=1600)})
    msgs = (res.get("messages") if isinstance(res, dict) else None) or [{}]
    if msgs[0].get("status") not in (None, "0"): raise ValueError(f"Vonage refused the message: {str(msgs[0].get('error-text') or 'unknown error')[:200]}")
    return {"message_id": msgs[0].get("message-id")}


def _webhook(secret: str, hosts: tuple[str, ...], label: str) -> str:
    """A chat webhook address kept as the credential. Only the service's own hosts are accepted."""
    host = (urlparse(secret).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in hosts): raise ValueError(f"The {label} credential must be the webhook address from {label} ({', '.join(hosts)})")
    return safe_url(secret)


def _basic(user: str, pw: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def _pair(secret: str, name: str, left: str, right: str) -> tuple[str, str]:
    a, _, b = secret.partition(":")
    if not a or not b: raise ValueError(f"The {name} credential must look like {left}:{right}")
    return a, b


def app_msteams(op: str, secret: str, f: dict) -> Any:
    url = _webhook(secret, ("webhook.office.com", "outlook.office.com", "logic.azure.com", "powerplatform.com"), "Microsoft Teams")
    do_request("POST", url, {}, json={"text": _text(f, "text", "Message", maxlen=20000)})
    return {"sent": True}


def app_googlechat(op: str, secret: str, f: dict) -> Any:
    url = _webhook(secret, ("chat.googleapis.com",), "Google Chat")
    res = do_request("POST", url, {}, json={"text": _text(f, "text", "Message", maxlen=4000)})
    return {"name": res.get("name")} if isinstance(res, dict) else {"sent": True}


def app_datadog(op: str, secret: str, f: dict) -> Any:
    site = _text(f, "site", "Site", required=False, maxlen=30).lower() or "datadoghq.com"
    if site not in ("datadoghq.com", "datadoghq.eu", "us3.datadoghq.com", "us5.datadoghq.com", "ap1.datadoghq.com"): raise ValueError("Site is datadoghq.com, datadoghq.eu, us3.datadoghq.com, us5.datadoghq.com or ap1.datadoghq.com")
    alert = _text(f, "alert_type", "Alert type", required=False, maxlen=10).lower() or "info"
    if alert not in ("error", "warning", "info", "success"): raise ValueError("Alert type is error, warning, info or success")
    body: dict[str, Any] = {"title": _text(f, "title", "Title", maxlen=100), "text": _text(f, "text", "Text", maxlen=4000), "alert_type": alert}
    tags = f.get("tags")
    if tags:
        if not isinstance(tags, list) or len(tags) > 20 or not all(isinstance(x, str) and len(x) <= 100 for x in tags): raise ValueError("Tags is a list of up to 20 short texts")
        body["tags"] = tags
    res = do_request("POST", f"https://api.{site}/api/v1/events", {"DD-API-KEY": secret}, json=body)
    ev = (res.get("event") if isinstance(res, dict) else None) or {}
    return {"id": ev.get("id"), "url": ev.get("url")}


def app_segment(op: str, secret: str, f: dict) -> Any:
    body = {"event": _event_name(f), "userId": _text(f, "user_id", "User ID", maxlen=200), "properties": _object(f, "properties", "Properties")}
    res = do_request("POST", "https://api.segment.io/v1/track", _basic(secret, ""), json=body)
    return {"success": res.get("success")} if isinstance(res, dict) else res


def app_webex(op: str, secret: str, f: dict) -> Any:
    room = _text(f, "room_id", "Room ID", maxlen=300)
    if not re.fullmatch(r"[A-Za-z0-9_=\-]{20,300}", room): raise ValueError("Room ID is the long id of the space")
    res = do_request("POST", "https://webexapis.com/v1/messages", {"Authorization": f"Bearer {secret}"}, json={"roomId": room, "text": _text(f, "text", "Message", maxlen=7000)})
    return {"id": res.get("id")} if isinstance(res, dict) else res


def app_rocketchat(op: str, secret: str, f: dict) -> Any:
    uid, token = _pair(secret, "Rocket.Chat", "USER_ID", "AUTH_TOKEN")
    channel = _text(f, "channel", "Channel", maxlen=100)
    if not re.fullmatch(r"[#@][A-Za-z0-9_.\-]{1,99}", channel): raise ValueError("Channel looks like #general or @someone")
    res = do_request("POST", f"{_origin(f)}/api/v1/chat.postMessage", {"X-User-Id": uid, "X-Auth-Token": token}, json={"channel": channel, "text": _text(f, "text", "Message", maxlen=5000)})
    return {"ok": res.get("success")} if isinstance(res, dict) else res


def app_pushbullet(op: str, secret: str, f: dict) -> Any:
    body = {"type": "note", "title": _text(f, "title", "Title", required=False, maxlen=200), "body": _text(f, "body", "Message", maxlen=4000)}
    res = do_request("POST", "https://api.pushbullet.com/v2/pushes", {"Access-Token": secret}, json=body)
    return {"iden": res.get("iden")} if isinstance(res, dict) else res


def app_messagebird(op: str, secret: str, f: dict) -> Any:
    to, sender = _text(f, "to", "To", maxlen=20).lstrip("+"), _text(f, "from", "From", maxlen=11)
    if not re.fullmatch(r"\d{6,15}", to): raise ValueError("To is a phone number with country code, for example 15551230000")
    if not re.fullmatch(r"[A-Za-z0-9]{1,16}", sender): raise ValueError("From is a number or a short name (letters and numbers)")
    res = do_request("POST", "https://rest.messagebird.com/messages", {"Authorization": f"AccessKey {secret}"}, json={"originator": sender, "recipients": [to], "body": _text(f, "text", "Message", maxlen=1600)})
    return {"id": res.get("id")} if isinstance(res, dict) else res


def app_mailjet(op: str, secret: str, f: dict) -> Any:
    key, sec = _pair(secret, "Mailjet", "API_KEY", "SECRET_KEY")
    to, sender, subject, text = _mail_fields(f)
    res = do_request("POST", "https://api.mailjet.com/v3.1/send", _basic(key, sec), json={"Messages": [{"From": {"Email": sender.split("<")[-1].rstrip(">").strip()}, "To": [{"Email": to}], "Subject": subject, "TextPart": text}]})
    msgs = (res.get("Messages") if isinstance(res, dict) else None) or [{}]
    return {"status": msgs[0].get("Status")}


def app_loops(op: str, secret: str, f: dict) -> Any:
    body: dict[str, Any] = {"email": _email(f)}
    for key, label in (("first_name", "First name"), ("last_name", "Last name"), ("user_group", "User group")):
        v = _text(f, key, label, required=False, maxlen=100)
        if v: body[{"first_name": "firstName", "last_name": "lastName", "user_group": "userGroup"}[key]] = v
    res = do_request("POST", "https://app.loops.so/api/v1/contacts/create", {"Authorization": f"Bearer {secret}"}, json=body)
    return {"success": res.get("success"), "id": res.get("id")} if isinstance(res, dict) else res


def app_buttondown(op: str, secret: str, f: dict) -> Any:
    res = do_request("POST", "https://api.buttondown.email/v1/subscribers", {"Authorization": f"Token {secret}"}, json={"email_address": _email(f)})
    return {"id": res.get("id")} if isinstance(res, dict) else res


def app_baserow(op: str, secret: str, f: dict) -> Any:
    table = _text(f, "table_id", "Table ID", maxlen=12)
    if not table.isdigit(): raise ValueError("Table ID is a number from the table's address")
    row = _object(f, "row", "Row", required=True)
    if not row: raise ValueError("Row needs at least one field")
    origin = _origin(f) if f.get("server") else "https://api.baserow.io"
    res = do_request("POST", f"{origin}/api/database/rows/table/{table}/?user_field_names=true", {"Authorization": f"Token {secret}"}, json=row)
    return {"id": res.get("id")} if isinstance(res, dict) else res


def app_healthchecks(op: str, secret: str, f: dict) -> Any:
    check = _text(f, "check_id", "Check ID", maxlen=36)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", check): raise ValueError("Check ID is the UUID in the check's ping address")
    status = _text(f, "status", "Status", required=False, maxlen=5).lower()
    if status not in ("", "start", "fail"): raise ValueError("Status is empty (success), start or fail")
    res = do_request("GET", f"https://hc-ping.com/{check}" + (f"/{status}" if status else ""), {})
    return {"ok": str(res).strip() == "OK"}


def app_mastodon(op: str, secret: str, f: dict) -> Any:
    vis = _text(f, "visibility", "Visibility", required=False, maxlen=10).lower() or "unlisted"
    if vis not in ("public", "unlisted", "private", "direct"): raise ValueError("Visibility is public, unlisted, private or direct")
    res = do_request("POST", f"{_origin(f)}/api/v1/statuses", {"Authorization": f"Bearer {secret}"}, json={"status": _text(f, "status", "Status", maxlen=5000), "visibility": vis})
    return {"id": res.get("id"), "url": res.get("url")} if isinstance(res, dict) else res


def app_matrix(op: str, secret: str, f: dict) -> Any:
    room = _text(f, "room_id", "Room ID", maxlen=200)
    if not re.fullmatch(r"![A-Za-z0-9]{1,100}:[A-Za-z0-9.\-]{1,100}", room): raise ValueError("Room ID looks like !abcdef:example.org (room settings, Advanced)")
    res = do_request("PUT", f"{_origin(f)}/_matrix/client/v3/rooms/{quote(room, safe='')}/send/m.room.message/{uuid.uuid4().hex}", {"Authorization": f"Bearer {secret}"}, json={"msgtype": "m.text", "body": _text(f, "text", "Message", maxlen=16000)})
    return {"event_id": res.get("event_id")} if isinstance(res, dict) else res


def app_telnyx(op: str, secret: str, f: dict) -> Any:
    to, sender = _text(f, "to", "To", maxlen=20), _text(f, "from", "From", maxlen=20)
    for label, num in (("To", to), ("From", sender)):
        if not re.fullmatch(r"\+\d{6,15}", num): raise ValueError(f"{label} is a phone number in international form, like +15551230000")
    res = do_request("POST", "https://api.telnyx.com/v2/messages", {"Authorization": f"Bearer {secret}"}, json={"from": sender, "to": to, "text": _text(f, "text", "Message", maxlen=1600)})
    return {"id": (res.get("data") or {}).get("id")} if isinstance(res, dict) else res


def app_zulip(op: str, secret: str, f: dict) -> Any:
    email, key = _pair(secret, "Zulip", "BOT_EMAIL", "API_KEY")
    kind = _text(f, "type", "Type", required=False, maxlen=7).lower() or "stream"
    if kind not in ("stream", "direct"): raise ValueError("Type is stream or direct")
    data = {"type": kind, "to": _text(f, "to", "To", maxlen=200), "content": _text(f, "content", "Message", maxlen=10000)}
    if kind == "stream": data["topic"] = _text(f, "topic", "Topic", maxlen=60)
    res = do_request("POST", f"{_origin(f)}/api/v1/messages", _basic(email, key), data=data)
    return {"id": res.get("id")} if isinstance(res, dict) else res


def app_ifttt(op: str, secret: str, f: dict) -> Any:
    event = _text(f, "event", "Event", maxlen=100)
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,100}", event): raise ValueError("Event name uses letters, numbers, _ and - only")
    body = {f"value{i}": _text(f, f"value{i}", f"Value {i}", required=False, maxlen=2000) for i in (1, 2, 3)}
    res = do_request("POST", f"https://maker.ifttt.com/trigger/{event}/with/key/{quote(secret, safe='')}", {}, json={k: v for k, v in body.items() if v})
    return {"message": str(res)[:200]}


def app_uptimekuma(op: str, secret: str, f: dict) -> Any:
    token = _text(f, "token", "Push token", maxlen=64)
    if not re.fullmatch(r"[A-Za-z0-9]{8,64}", token): raise ValueError("Push token is the code in the monitor's push address")
    status = _text(f, "status", "Status", required=False, maxlen=4).lower() or "up"
    if status not in ("up", "down"): raise ValueError("Status is up or down")
    res = do_request("GET", f"{_origin(f)}/api/push/{token}", {}, params={"status": status, "msg": _text(f, "message", "Message", required=False, maxlen=200) or "OK"})
    return {"ok": res.get("ok")} if isinstance(res, dict) else res


APP_HANDLERS = {"msteams": app_msteams, "googlechat": app_googlechat, "datadog": app_datadog, "segment": app_segment, "webex": app_webex, "rocketchat": app_rocketchat, "pushbullet": app_pushbullet, "messagebird": app_messagebird, "mailjet": app_mailjet, "loops": app_loops, "buttondown": app_buttondown, "baserow": app_baserow, "healthchecks": app_healthchecks, "mastodon": app_mastodon, "matrix": app_matrix, "telnyx": app_telnyx, "zulip": app_zulip, "ifttt": app_ifttt, "uptimekuma": app_uptimekuma, "mattermost": app_mattermost, "gotify": app_gotify, "cloudflare": app_cloudflare, "posthog": app_posthog, "mixpanel": app_mixpanel, "vonage": app_vonage, "pagerduty": app_pagerduty, "opsgenie": app_opsgenie, "intercom": app_intercom, "freshdesk": app_freshdesk, "supabase": app_supabase, "bitly": app_bitly, "convertkit": app_convertkit, "brevo": app_brevo, "mailgun": app_mailgun, "telegram": app_telegram, "github": app_github, "shopify": app_shopify, "zendesk": app_zendesk, "pipedrive": app_pipedrive, "clickup": app_clickup, "monday": app_monday, "dropbox": app_dropbox, "resend": app_resend, "postmark": app_postmark, "whatsapp": app_whatsapp, "pushover": app_pushover, "gcalendar": app_gcalendar, "gdrive": app_gdrive, "sendgrid": app_sendgrid, "mailchimp": app_mailchimp, "trello": app_trello, "asana": app_asana, "jira": app_jira, "todoist": app_todoist, "gitlab": app_gitlab, "discord": app_discord, "ntfy": app_ntfy, "slack": app_slack, "notion": app_notion, "airtable": app_airtable, "stripe": app_stripe, "twilio": app_twilio, "linear": app_linear, "hubspot": app_hubspot}


def connector_call(d: dict, previous: Any) -> Any:
    """Call an operation of a connector the owner described in a manifest (Settings, Custom connectors)."""
    name, op = str(d.get("connector") or "").strip(), str(d.get("operation") or "").strip()
    with conn() as db: row = db.execute("SELECT manifest FROM connectors WHERE name=?", (name,)).fetchone()
    if not row: raise ValueError(f"Unknown connector: {name or '(none chosen)'}. Add it in Settings, under Custom connectors.")
    manifest = json.loads(row["manifest"])
    fields = read_json(render_template(str(d.get("fields") or "{}"), previous, json_escape=True))
    if not isinstance(fields, dict): raise ValueError("Fields must be a JSON object")
    req = connector_lib.build_request(manifest, op, fields)
    safe_url(req["url"])
    headers, params, auth = {}, dict(req["params"]), manifest["auth"]
    if auth["type"] != "none":
        secret = lookup_secret(str(d.get("credential") or "").strip())
        if auth["type"] == "bearer": headers["Authorization"] = f"Bearer {secret}"
        elif auth["type"] == "basic": headers["Authorization"] = "Basic " + base64.b64encode(secret.encode()).decode()
        elif auth["type"] == "header": headers[auth["name"]] = secret
        else: params[auth["name"]] = secret
    kwargs: dict = {"params": params or None}
    if "json" in req: kwargs["json"] = req["json"]
    res = do_request(req["method"], req["url"], headers, **kwargs)
    return get_path(res, req["result"]) if req["result"] else res


def app_action(d: dict, previous: Any) -> Any:
    if str(d.get("service") or "").strip() == "connector": return connector_call(d, previous)
    service, op = str(d.get("service") or "").strip(), str(d.get("operation") or "").strip()
    if service not in APP_OPS: raise ValueError(f"Choose a service ({', '.join(APP_OPS)})")
    if op not in APP_OPS[service]: raise ValueError(f"Unknown {service} action: {op or '(none)'}")
    cred = str(d.get("credential") or "").strip()
    secret = oauth_access_token(cred) if service in OAUTH_SERVICES else lookup_secret(cred) if cred or service not in OPTIONAL_CREDENTIAL else ""
    fields = read_json(render_template(str(d.get("fields") or "{}"), previous, json_escape=True))
    if not isinstance(fields, dict): raise ValueError("Fields must be a JSON object")
    return APP_HANDLERS[service](op, secret, fields)


# ---------------------------------------------------------------------------
# Item tools: small nodes that reshape lists and values (sort, limit, remove duplicates, split, summarise, rename, date/time, crypto)
# ---------------------------------------------------------------------------
def _need_list(previous: Any, label: str) -> list:
    if not isinstance(previous, list): raise ValueError(f"{label} needs a list as input")
    return previous


def _sort_key(value: Any):
    if isinstance(value, bool): return (1, str(value))
    if isinstance(value, (int, float)): return (0, float(value), "")
    if isinstance(value, str): return (1, 0.0, value.lower())
    if value is None: return (3, 0.0, "")
    return (2, 0.0, json.dumps(value, sort_keys=True, default=str))


def tool_sort(d: dict, previous: Any) -> list:
    items, path = _need_list(previous, "Sort"), str(d.get("path") or "").strip()
    if len(items) > 50_000: raise ValueError("Sort is limited to 50000 items")
    return sorted(items, key=lambda it: _sort_key(get_path(it, path) if path else it), reverse=str(d.get("order") or "asc") == "desc")


def tool_limit(d: dict, previous: Any) -> list:
    items = _need_list(previous, "Limit")
    try: count = max(0, int(float(d.get("count") or 10)))
    except (TypeError, ValueError): raise ValueError("Count must be a number")
    return items[-count:] if count and str(d.get("from") or "first") == "last" else items[:count]


def tool_dedupe(d: dict, previous: Any) -> list:
    items, path, seen, out = _need_list(previous, "Remove Duplicates"), str(d.get("path") or "").strip(), set(), []
    for it in items:
        key = json.dumps(get_path(it, path) if path else it, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def tool_split(d: dict, previous: Any) -> list:
    value = get_path(previous, str(d.get("path") or "").strip())
    if not isinstance(value, list): raise ValueError("Split Out found no list at that field")
    return value


def tool_aggregate(d: dict, previous: Any) -> Any:
    items, path, op = _need_list(previous, "Summarize"), str(d.get("path") or "").strip(), str(d.get("operation") or "count")
    values = [get_path(it, path) if path else it for it in items]
    if op == "count": return len(values)
    if op == "collect": return values
    nums = []
    for v in values:
        if v is None or v == "": continue
        try: nums.append(float(v))
        except (TypeError, ValueError): raise ValueError(f"Cannot {op} the value {str(v)[:40]!r}: it is not a number")
    if op not in ("sum", "avg", "min", "max"): raise ValueError(f"Unknown summary: {op}")
    if not nums: return 0 if op == "sum" else None
    res = {"sum": sum(nums), "avg": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}[op]
    return int(res) if float(res).is_integer() else round(res, 10)


def tool_rename(d: dict, previous: Any) -> Any:
    mapping = read_json(str(d.get("mapping") or "{}"))
    if not isinstance(mapping, dict) or not all(isinstance(v, str) and v for v in mapping.values()): raise ValueError('Mapping must be a JSON object like {"old_name": "new_name"}')
    one = lambda obj: {mapping.get(k, k): v for k, v in obj.items()} if isinstance(obj, dict) else obj
    return [one(x) for x in previous] if isinstance(previous, list) else one(previous)


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool): return datetime.fromtimestamp(float(value), timezone.utc)
    text = str(value if value is not None else "").strip()
    if not text: return datetime.now(timezone.utc)
    if re.fullmatch(r"-?\d+(\.\d+)?", text): return datetime.fromtimestamp(float(text), timezone.utc)
    try: dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError: raise ValueError(f"Cannot read {text[:40]!r} as a date (use 2025-01-31 or 2025-01-31T10:00:00Z)")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


DT_UNITS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800}


def tool_datetime(d: dict, previous: Any) -> Any:
    op = str(d.get("operation") or "now")
    value = _to_datetime(render_template(str(d.get("value") or ""), previous))
    unit = str(d.get("unit") or "days")
    if unit not in DT_UNITS: raise ValueError(f"Unit must be one of {', '.join(DT_UNITS)}")
    if op == "now": return datetime.now(timezone.utc).isoformat()
    if op in ("add", "subtract"):
        try: amount = float(render_template(str(d.get("amount") or "0"), previous))
        except ValueError: raise ValueError("Amount must be a number")
        if abs(amount * DT_UNITS[unit]) > 3650 * 86400 * 10: raise ValueError("Amount is too large")
        return (value + timedelta(seconds=amount * DT_UNITS[unit] * (1 if op == "add" else -1))).isoformat()
    if op == "diff":
        other = _to_datetime(render_template(str(d.get("other") or ""), previous))
        res = (other - value).total_seconds() / DT_UNITS[unit]
        return int(res) if float(res).is_integer() else round(res, 6)
    if op == "format":
        fmt = str(d.get("format") or "%Y-%m-%d")
        if len(fmt) > 100: raise ValueError("Format is too long")
        return value.strftime(fmt)
    raise ValueError(f"Unknown date operation: {op}")


def tool_crypto(d: dict, previous: Any) -> Any:
    op = str(d.get("operation") or "sha256")
    if op == "uuid": return str(uuid.uuid4())
    if op == "random":
        try: n = max(1, min(int(float(d.get("value") or 16)), 128))
        except (TypeError, ValueError): raise ValueError("Length must be a number")
        return secrets.token_urlsafe(n)[:n]
    data = render_template(str(d.get("value") or ""), previous)
    if len(data) > 1_000_000: raise ValueError("Input is too large (limit 1 MB)")
    raw = data.encode("utf-8")
    if op in ("sha256", "sha512"): return hashlib.new(op, raw).hexdigest()
    if op in ("sha1", "md5"): return hashlib.new(op, raw, usedforsecurity=False).hexdigest()
    if op == "hmac_sha256":
        key = render_template(str(d.get("key") or ""), previous)
        if not key: raise ValueError("HMAC needs a secret key (use {{cred.NAME}} for a saved credential)")
        return hmac.new(key.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    if op == "base64_encode": return base64.b64encode(raw).decode()
    if op == "base64_decode":
        try: return base64.b64decode(data.strip(), validate=True).decode("utf-8")
        except Exception: raise ValueError("That is not valid base64 text")
    raise ValueError(f"Unknown operation: {op}")


class _TextExtractor(HTMLParser):
    """Collects the text, or an attribute, of elements matching a simple selector: tag, .class, #id, tag.class or tag#id."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, tag: str, cls: str, ident: str, attr: str):
        super().__init__(convert_charrefs=True)
        self.tag, self.cls, self.ident, self.attr = tag, cls, ident, attr
        self.out: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.buf: list[str] | None = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hit = (not self.tag or tag == self.tag) and (not self.cls or self.cls in (a.get("class") or "").split()) and (not self.ident or a.get("id") == self.ident)
        if self.buf is not None and tag not in self.VOID: self.depth += 1
        if hit and self.buf is None:
            if self.attr:
                if a.get(self.attr) is not None: self.out.append(a[self.attr])
            elif tag not in self.VOID:
                self.buf, self.depth = [], 1

    def handle_endtag(self, tag):
        if self.buf is None: return
        self.depth -= 1
        if self.depth <= 0:
            self.out.append(re.sub(r"\s+", " ", "".join(self.buf)).strip())
            self.buf = None

    def handle_data(self, data):
        if self.buf is not None: self.buf.append(data)


def tool_convert(d: dict, previous: Any) -> Any:
    op = str(d.get("operation") or "json_parse")
    data = render_template(str(d.get("value") or ""), previous)
    if len(data) > 2_000_000: raise ValueError("Input is too large (limit 2 MB)")
    if op == "json_parse":
        try: return json.loads(data)
        except ValueError: raise ValueError("That text is not valid JSON")
    if op == "json_stringify":
        # The value is rendered text, so a JSON document typed into the field is re-encoded compactly.
        try: return json.dumps(json.loads(data), separators=(",", ":"), ensure_ascii=False)
        except ValueError: return json.dumps(data, ensure_ascii=False)
    if op == "url_encode": return quote(data, safe="")
    if op == "url_decode": return unquote(data)
    if op == "xml_to_json":
        if re.search(r"<!(DOCTYPE|ENTITY)", data, re.I): raise ValueError("XML with a DOCTYPE or entity definitions is not accepted")
        try: root = ET.fromstring(data)
        except ET.ParseError as e: raise ValueError(f"That text is not valid XML ({e})")
        def conv(el):
            node: dict[str, Any] = {f"@{k}": v for k, v in el.attrib.items()}
            kids: dict[str, list] = {}
            for c in el: kids.setdefault(re.sub(r"^\{.*\}", "", c.tag), []).append(conv(c))
            for k, v in kids.items(): node[k] = v[0] if len(v) == 1 else v
            text = (el.text or "").strip()
            if not node: return text
            if text: node["#text"] = text
            return node
        return {re.sub(r"^\{.*\}", "", root.tag): conv(root)}
    if op == "yaml_to_json":
        import yaml
        try:
            if any(isinstance(ev, yaml.events.AliasEvent) for ev in yaml.parse(data, Loader=yaml.SafeLoader)): raise ValueError("YAML aliases (*name) are not accepted")  # blocks expansion bombs
        except yaml.YAMLError as e: raise ValueError(f"That text is not valid YAML ({str(e).splitlines()[0][:100]})")
        try: out = yaml.safe_load(data)
        except yaml.YAMLError as e: raise ValueError(f"That text is not valid YAML ({str(e).splitlines()[0][:100]})")
        try: json.dumps(out)
        except (TypeError, ValueError): raise ValueError("That YAML uses a type that cannot be turned into data (dates and sets are not supported)")
        return out
    if op == "json_to_yaml":
        import yaml
        try: value = json.loads(data)
        except ValueError: raise ValueError("That text is not valid JSON")
        return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=False)
    if op == "pdf_text":
        from pypdf import PdfReader
        path = file_target(data.strip())
        if not path.is_file(): raise ValueError("That file does not exist in the files folder")
        if path.stat().st_size > FILE_MAX: raise ValueError("The PDF is larger than 5 MB")
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted: raise ValueError("That PDF is password protected")
            pages = [(p.extract_text() or "").strip() for p in reader.pages[:50]]
            count = len(reader.pages)
        except ValueError: raise
        except Exception: raise ValueError("Could not read that PDF")
        joined = "\n\n".join(pages)
        return {"text": joined[:200_000], "pages": count, "read_pages": len(pages), "truncated": count > 50 or len(joined) > 200_000}
    if op == "html_extract":
        sel = str(d.get("selector") or "").strip()
        m = re.fullmatch(r"([A-Za-z][A-Za-z0-9]*)?(?:\.([A-Za-z0-9_\-]+))?(?:#([A-Za-z0-9_\-]+))?", sel)
        if not sel or not m: raise ValueError("Selector is a tag, .class or #id, like a, h1.title or #price")
        attr = str(d.get("attribute") or "").strip().lower()
        if attr and not re.fullmatch(r"[a-z][a-z0-9\-]{0,40}", attr): raise ValueError("Attribute is a name like href or src")
        ex = _TextExtractor((m.group(1) or "").lower(), m.group(2) or "", m.group(3) or "", attr)
        ex.feed(data); ex.close()
        return ex.out[:1000]
    raise ValueError(f"Unknown operation: {op}")


TABLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
MAX_TABLE_ROWS, MAX_TABLES, MAX_ROW_BYTES, MAX_TABLE_READ = 10_000, 50, 65_536, 1000


def _table_json(d: dict, key: str, previous: Any, label: str, required: bool = False) -> dict:
    text = str(d.get(key) or "").strip()
    if not text:
        if required: raise ValueError(f"{label} is required")
        return {}
    value = read_json(render_template(text, previous, json_escape=True))
    if not isinstance(value, dict): raise ValueError(f"{label} must be a JSON object")
    return value


def _row_matches(data: dict, flt: dict) -> bool:
    for path, want in flt.items():
        got = get_path(data, str(path))
        if got != want and str(got) != str(want): return False
    return True


def table_scope(project_id: str | None) -> tuple[str, tuple]:
    """The SQL condition and parameters that limit a query to one project's tables (None means workflows without a project)."""
    return ("project_id IS NULL", ()) if project_id is None else ("project_id=?", (project_id,))


def _table_project() -> str | None:
    """Tables belong to the project of the workflow that is running (a Run Workflow call uses the called workflow's project)."""
    stack = _stack_var.get()
    if not stack: return None
    with conn() as db: row = db.execute("SELECT project_id FROM workflows WHERE id=?", (stack[-1],)).fetchone()
    return row["project_id"] if row else None


def tool_table(d: dict, previous: Any) -> Any:
    """Built-in data tables: small named tables stored with FlowPilot, so workflows can remember things between runs."""
    op, name = str(d.get("operation") or "find"), str(d.get("table") or "").strip()
    if not TABLE_NAME_RE.fullmatch(name): raise ValueError("Table name starts with a letter and uses letters, numbers and _ (up to 40 characters)")
    try: limit = max(1, min(int(float(d.get("limit") or 100)), MAX_TABLE_READ))
    except (TypeError, ValueError): raise ValueError("Limit must be a number")
    flt = _table_json(d, "filter", previous, "Filter")
    shown = lambda r: {"_id": r["id"], **json.loads(r["data"])}
    pid = _table_project()
    sc, sp = table_scope(pid)
    with conn() as db:
        def room(adding: int) -> None:
            if not db.execute(f"SELECT 1 FROM data_rows WHERE tbl=? AND {sc} LIMIT 1", (name, *sp)).fetchone() and db.execute("SELECT COUNT(*) AS n FROM (SELECT DISTINCT tbl, project_id FROM data_rows) t").fetchone()["n"] >= MAX_TABLES: raise ValueError(f"At most {MAX_TABLES} tables")
            if db.execute(f"SELECT COUNT(*) AS n FROM data_rows WHERE tbl=? AND {sc}", (name, *sp)).fetchone()["n"] + adding > MAX_TABLE_ROWS: raise ValueError(f"A table holds at most {MAX_TABLE_ROWS} rows")

        def encode(row: Any) -> str:
            if not isinstance(row, dict) or not row: raise ValueError("A row must be a JSON object with at least one field")
            text = json.dumps(row, default=str)
            if len(text.encode()) > MAX_ROW_BYTES: raise ValueError("A row is too large (limit 64 KB)")
            return text

        def matching() -> list:
            rows = db.execute(f"SELECT id, data FROM data_rows WHERE tbl=? AND {sc} ORDER BY id", (name, *sp)).fetchall()
            return [r for r in rows if _row_matches(json.loads(r["data"]), flt)]

        if op == "insert":
            text = encode(_table_json(d, "row", previous, "Row", required=True))
            room(1)
            cur = db.execute("INSERT INTO data_rows (tbl, data, created_at, project_id) VALUES (?,?,?,?)", (name, text, now(), pid))
            return {"_id": cur.lastrowid, **json.loads(text)}
        if op == "insert_all":
            if not isinstance(previous, list) or not previous: raise ValueError("Insert every item needs a list of objects as input")
            if len(previous) > 1000: raise ValueError("Insert at most 1000 rows at a time")
            texts = [encode(x) for x in previous]
            room(len(texts))
            db.executemany("INSERT INTO data_rows (tbl, data, created_at, project_id) VALUES (?,?,?,?)", [(name, x, now(), pid) for x in texts])
            return {"inserted": len(texts)}
        if op == "find":
            found = matching()
            if str(d.get("order") or "oldest") == "newest": found.reverse()
            return [shown(r) for r in found[:limit]]
        if op == "count": return {"count": len(matching())}
        if op == "get":
            rid = render_template(str(d.get("row_id") or ""), previous).strip()
            if not rid.isdigit(): raise ValueError("Row ID is a number")
            r = db.execute(f"SELECT id, data FROM data_rows WHERE tbl=? AND id=? AND {sc}", (name, int(rid), *sp)).fetchone()
            return shown(r) if r else None
        if op == "update":
            changes = _table_json(d, "values", previous, "Values", required=True)
            if not changes: raise ValueError("Values needs at least one field")
            if not flt: raise ValueError("Update needs a filter, so it cannot change every row by accident")
            hits = matching()[:MAX_TABLE_READ]
            for r in hits: db.execute("UPDATE data_rows SET data=? WHERE id=?", (encode({**json.loads(r["data"]), **changes}), r["id"]))
            return {"updated": len(hits)}
        if op == "delete":
            if not flt: raise ValueError("Delete needs a filter. To remove everything, choose Empty the table")
            hits = matching()
            db.executemany("DELETE FROM data_rows WHERE id=?", [(r["id"],) for r in hits])
            return {"deleted": len(hits)}
        if op == "clear":
            return {"deleted": db.execute(f"DELETE FROM data_rows WHERE tbl=? AND {sc}", (name, *sp)).rowcount}
    raise ValueError(f"Unknown operation: {op}")


ITEM_TOOLS = {"sort": tool_sort, "limit": tool_limit, "dedupe": tool_dedupe, "split": tool_split, "aggregate": tool_aggregate, "rename": tool_rename, "datetime": tool_datetime, "crypto": tool_crypto, "convert": tool_convert, "table": tool_table}


def param_text(value: Any) -> str:
    """A value as text for a query string or a form field: true and false in lower case, JSON for lists and objects."""
    if value is None: return ""
    if isinstance(value, bool): return "true" if value else "false"
    return json.dumps(value) if isinstance(value, (dict, list)) else str(value)


MAX_PAGINATED_ITEMS = 5000


def paginate_http(method: str, url: str, headers: dict, kwargs: dict, d: dict) -> list:
    """Follow a paged API and return every item. Modes: next_url (the reply holds the next address) or page (a page-number parameter)."""
    mode = str(d.get("paginate"))
    if mode not in ("next_url", "page"): raise ValueError("Pagination must be none, next_url or page")
    items_path, next_path = str(d.get("items_path") or "").strip(), str(d.get("next_path") or "").strip()
    page_param = str(d.get("page_param") or "page").strip() or "page"
    try: first_page = int(float(d.get("first_page") or 1))
    except (TypeError, ValueError): raise ValueError("First page must be a number")
    try: max_pages = max(1, min(int(float(d.get("max_pages") or 5)), 20))
    except (TypeError, ValueError): raise ValueError("Maximum pages must be a number")
    if mode == "next_url" and not next_path: raise ValueError("Tell FlowPilot where the next address is in each reply (for example next or links.next)")
    host, current, results = urlparse(url).hostname, url, []
    base_params = dict(kwargs.get("params") or {})
    for page in range(max_pages):
        kw = dict(kwargs)
        if mode == "page": kw["params"] = {**base_params, page_param: str(first_page + page)}
        elif page > 0: kw.pop("params", None)  # the next address already carries its own query
        res = do_request(method, current, headers, **kw)
        items = get_path(res, items_path) if items_path else res
        if not isinstance(items, list): raise ValueError("Page " + str(page + 1) + " has no list" + (f" at {items_path!r}" if items_path else "") + ". Set where the items are.")
        results.extend(items)
        if len(results) > MAX_PAGINATED_ITEMS: raise ValueError(f"More than {MAX_PAGINATED_ITEMS} items. Lower the maximum pages.")
        if mode == "page":
            if not items: break
            continue
        nxt = get_path(res, next_path) if isinstance(res, (dict, list)) else None
        if not nxt: break
        nxt_url = safe_url(urljoin(current, str(nxt)))
        if urlparse(nxt_url).hostname != host: raise ValueError("The next page is on a different site, so it was not followed")
        current = nxt_url
    return results


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
def ai_connection(d: dict) -> tuple[str, str, str]:
    """The chat endpoint, model and key from an AI credential."""
    cred = urlparse(lookup_secret(str(d.get("credential") or "").strip()))
    hint = "The AI credential must look like https://host/v1?model=NAME&key=TOKEN (for a local model: http://127.0.0.1:11434/v1?model=llama3.2)"
    if cred.scheme not in ("http", "https") or not cred.hostname: raise ValueError(hint)
    query = parse_qs(cred.query)
    model, key = query.get("model", [""])[0], query.get("key", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_.:/@\-]{1,100}", model): raise ValueError(hint)
    if cred.scheme == "http" and os.getenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS") != "1":
        raise ValueError("Local models use http, which needs FLOWPILOT_ALLOW_PRIVATE_HOSTS=1 when you start the API. This switch never applies to the HTTP node.")
    assert_public_host(cred.hostname.lower(), relaxed=True)
    return f"{cred.scheme}://{cred.hostname}{':' + str(cred.port) if cred.port else ''}{cred.path.rstrip('/')}", model, key


_ai_budget_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar("fp_ai_budget", default=None)


def _chat(base: str, model: str, key: str, messages: list[dict], temperature: float) -> str:
    budget = _ai_budget_var.get()
    if budget is not None:  # the workflow's own limits per run (a guard against loops that spend money); checked before each call
        if budget["limit"] and budget["used"] >= budget["limit"]: raise ValueError(f"This workflow's limit of {budget['limit']} AI calls per run was reached")
        if budget["token_limit"] and budget["tokens"] >= budget["token_limit"]: raise ValueError(f"This workflow's limit of {budget['token_limit']} AI tokens per run was reached")
        budget["used"] += 1
    res = do_request("POST", base + "/chat/completions", {"Authorization": f"Bearer {key}"} if key else {},
                     json={"model": model, "messages": messages, "temperature": temperature, "stream": False}, timeout=90.0)
    try: text = str(res["choices"][0]["message"]["content"])
    except (TypeError, KeyError, IndexError): raise ValueError("The AI service returned an unexpected reply")
    if len(text) > 200_000: raise ValueError("The AI reply is too large")
    if budget is not None:
        usage = res.get("usage") if isinstance(res, dict) else None
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        budget["tokens"] += int(total) if isinstance(total, (int, float)) and total >= 0 else (len(text) + sum(len(str(m.get("content", ""))) for m in messages)) // 4  # a rough estimate when the service reports nothing
    return text


def _json_reply(text: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try: return json.loads(cleaned)
    except ValueError: raise ValueError("The AI reply was not valid JSON. Ask for JSON only in the prompt.")


AI_TASKS = ("prompt", "extract", "classify", "sentiment", "summarize", "translate", "rewrite")
REWRITE_STYLES = {"professional": "a professional, polite tone", "friendly": "a warm, friendly tone", "concise": "as few words as possible", "simple": "plain, simple language", "formal": "a formal tone"}
EXTRACT_TYPES = ("text", "number", "integer", "true_false", "list")


def _coerce(value: Any, kind: str) -> Any:
    if value is None or value == "": return None
    try:
        if kind == "number": return float(str(value).replace(",", "")) if not isinstance(value, (int, float)) or isinstance(value, bool) else value
        if kind == "integer": return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError): return None
    if kind == "true_false":
        if isinstance(value, bool): return value
        return {"true": True, "yes": True, "false": False, "no": False}.get(str(value).strip().lower())
    if kind == "list": return value if isinstance(value, list) else [value]
    return value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def ai_extract_schema(d: dict) -> dict[str, tuple[str, str]]:
    """Fields to pull out of the text, from a JSON object of name -> description or {"type": ..., "description": ...}."""
    raw = read_json(str(d.get("schema") or ""))
    if not isinstance(raw, dict) or not 1 <= len(raw) <= 30: raise ValueError("Fields to extract is a JSON object with 1 to 30 entries, like {\"email\": \"the sender's email address\"}")
    out = {}
    for name, spec in raw.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", str(name)): raise ValueError(f"Field name {str(name)[:30]!r} must use letters, numbers and _")
        kind, desc = ("text", str(spec)) if not isinstance(spec, dict) else (str(spec.get("type") or "text"), str(spec.get("description") or ""))
        if kind not in EXTRACT_TYPES: raise ValueError(f"Field {name}: type must be one of {', '.join(EXTRACT_TYPES)}")
        out[str(name)] = (kind, desc[:300])
    return out


def ai_categories(d: dict) -> list[str]:
    cats = [c.strip() for c in str(d.get("categories") or "").split(",") if c.strip()]
    if not 2 <= len(cats) <= 20 or len(set(c.lower() for c in cats)) != len(cats) or any(len(c) > 60 for c in cats): raise ValueError("Categories is a comma-separated list of 2 to 20 different names")
    return cats


def run_ai(d: dict, previous: Any) -> Any:
    base, model, key = ai_connection(d)
    task = str(d.get("task") or "prompt")
    if task not in AI_TASKS: raise ValueError(f"Task must be one of: {', '.join(AI_TASKS)}")
    prompt = render_template(str(d.get("prompt", "")), previous)
    system = render_template(str(d.get("system", "")), previous)
    if not prompt.strip(): raise ValueError("Write a prompt" if task == "prompt" else "Provide the text to work on")
    if len(prompt) > 50_000 or len(system) > 10_000: raise ValueError("The prompt is too long")
    try: temperature = max(0.0, min(float(d.get("temperature") or 0.2), 2.0))
    except (TypeError, ValueError): raise ValueError("Temperature must be a number")
    if task == "extract":
        schema = ai_extract_schema(d)
        spec = "\n".join(f"- {n} ({k}): {desc}" for n, (k, desc) in schema.items())
        system = ("Extract information from the user's text. Reply with one JSON object only, no commentary, with exactly these keys:\n" + spec +
                  "\nUse null for anything the text does not say. Never invent values.")
    elif task in ("classify", "sentiment"):
        cats = ["positive", "neutral", "negative"] if task == "sentiment" else ai_categories(d)
        system = ("Classify the user's text into exactly one of these categories: " + ", ".join(cats) + '. Reply with one JSON object only: {"category": "<one of the categories>", "confidence": <number from 0 to 1>}.')
    elif task == "summarize":
        length = {"short": "one or two sentences", "medium": "a short paragraph", "long": "several paragraphs"}.get(str(d.get("length") or "short"))
        if not length: raise ValueError("Length must be short, medium or long")
        system = f"Summarize the user's text in {length}. Reply with the summary only."
    elif task == "translate":
        language = str(d.get("language") or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z \-()]{1,40}", language): raise ValueError("Language is the name of the target language, such as French or Brazilian Portuguese")
        system = f"Translate the user's text into {language}. Keep names, numbers and formatting. Reply with the translation only."
    elif task == "rewrite":
        style = REWRITE_STYLES.get(str(d.get("style") or "professional"))
        if not style: raise ValueError(f"Style must be one of: {', '.join(REWRITE_STYLES)}")
        system = f"Rewrite the user's text in {style}. Keep the meaning and every fact. Reply with the rewritten text only."
    messages = ([{"role": "system", "content": system}] if system.strip() else []) + [{"role": "user", "content": prompt}]
    text = _chat(base, model, key, messages, temperature)
    if task in ("extract", "classify", "sentiment"):
        try: _json_reply(text)
        except ValueError:  # one corrective retry: small models often wrap JSON in chatter
            text = _chat(base, model, key, messages + [{"role": "assistant", "content": text[:4000]}, {"role": "user", "content": "That was not valid JSON. Reply again with the JSON object only, no other text."}], 0.0)
    if task == "extract":
        got = _json_reply(text)
        if not isinstance(got, dict): raise ValueError("The AI reply was not a JSON object")
        return {name: _coerce(got.get(name), kind) for name, (kind, _) in schema.items()}
    if task in ("classify", "sentiment"):
        got = _json_reply(text)
        picked = str(got.get("category") if isinstance(got, dict) else got).strip()
        match = next((c for c in cats if c.lower() == picked.lower()), None)
        if match is None: raise ValueError(f"The AI chose {picked[:60]!r}, which is not one of the categories")
        try: confidence = max(0.0, min(float(got.get("confidence")), 1.0)) if isinstance(got, dict) and got.get("confidence") is not None else None
        except (TypeError, ValueError): confidence = None
        return {"category": match, "confidence": confidence, "model": model}
    if task in ("summarize", "translate", "rewrite"): return {"text": text.strip(), "model": model}
    if str(d.get("json_output") or "false").lower() == "true": return _json_reply(text)
    return {"text": text, "model": model}


# ---------------------------------------------------------------------------
# Workflow builder: describe what you want, a model drafts the workflow, and FlowPilot checks it before saving it as an inactive draft
# ---------------------------------------------------------------------------
BUILDER_CATALOG: dict[str, tuple[str, dict[str, str]]] = {
    "trigger": ("Start by clicking Run", {}),
    "schedule": ("Start on a timer", {"mode": "interval, daily or cron", "minutes": "for interval", "time": "HH:MM for daily", "cron": "5-field cron for cron", "timezone": "IANA name, optional"}),
    "webhook": ("Start when a URL is called; later nodes read {{body.field}}", {"sample": "test JSON payload"}),
    "input": ("Fixed JSON data", {"json": "a JSON object or list"}),
    "transform": ("Pick a value by path", {"path": "dotted path such as body.email"}),
    "template": ("Build text from {{fields}}", {"template": "text with {{placeholders}}"}),
    "set": ("Build an object from {{fields}}", {"json": "JSON object whose values may use {{placeholders}}"}),
    "filter": ("Keep list items that match", {"path": "field", "op": "equals, not_equals, contains, gt, lt, exists", "equals": "value"}),
    "condition": ("Branch true/false (edges use sourceHandle true or false)", {"path": "field", "op": "equals, not_equals, contains, gt, lt, exists", "equals": "value"}),
    "switch": ("Branch by value (edges use sourceHandle 1 to 4 or default)", {"path": "field", "case1": "value", "case2": "value"}),
    "http": ("Call a web API", {"method": "GET, POST, PUT, PATCH or DELETE", "url": "https address, may use {{placeholders}}", "body": "JSON text for POST/PUT/PATCH"}),
    "notify": ("Send a chat message (Slack, Discord or Teams webhook)", {"message": "text with {{placeholders}}"}),
    "email": ("Send an email by SMTP", {"to": "address", "subject": "text", "body": "text"}),
    "ai": ("Ask an AI model", {"task": "prompt, extract, classify, sentiment, summarize, translate or rewrite", "prompt": "text or {{field}}"}),
    "convert": ("Convert data", {"operation": "json_parse, json_stringify, xml_to_json, yaml_to_json, json_to_yaml, html_extract", "value": "{{field}}"}),
    "datetime": ("Date and time helper", {"operation": "now, add, format"}),
    "sort": ("Sort a list", {"path": "field"}),
    "limit": ("Keep the first N items", {"count": "number"}),
    "dedupe": ("Remove duplicates", {"path": "field"}),
    "loop": ("Run following steps once per list item", {}),
    "merge": ("Join branches", {"mode": "append, first or combine"}),
    "delay": ("Pause", {"seconds": "number"}),
    "output": ("Finish and show the result", {}),
}
BUILDER_TRIGGERS = ("trigger", "schedule", "webhook")
BUILDER_MAX_NODES = 25
BUILDER_DROP_KEYS = {"credential", "auth_credential", "pin", "error_workflow", "workflow_id", "workflows", "auth_type"}


def builder_prompt() -> str:
    lines = [f'- {typ}: {desc}' + (f' Fields: {", ".join(f"{k} ({v})" for k, v in fields.items())}.' if fields else "") for typ, (desc, fields) in BUILDER_CATALOG.items()]
    return ("You design automation workflows. Reply with one JSON object only, no commentary:\n"
            '{"name": "short title", "description": "one sentence", "nodes": [{"id": "n1", "type": "...", "label": "short name", "data": {...}}], "edges": [{"source": "n1", "target": "n2", "sourceHandle": "optional"}]}\n'
            f"Rules: exactly one starting node (trigger, schedule or webhook) and every other node reachable from it; at most {BUILDER_MAX_NODES} nodes; data values are strings or numbers; "
            "never put secrets, passwords or API keys in data; use {{field}} placeholders to pass values between steps; end with an output node. Available node types:\n" + "\n".join(lines))


def builder_clean(draft: Any) -> WorkflowIn:
    """Turn a model's draft into a checked workflow, or raise ValueError with a reason the model can be shown."""
    if not isinstance(draft, dict): raise ValueError("The reply must be one JSON object")
    raw_nodes, raw_edges = draft.get("nodes"), draft.get("edges")
    if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= BUILDER_MAX_NODES: raise ValueError(f"nodes must be a list of 1 to {BUILDER_MAX_NODES} items")
    if not isinstance(raw_edges, list) or len(raw_edges) > BUILDER_MAX_NODES * 2: raise ValueError("edges must be a list")
    nodes, ids = [], set()
    for i, n in enumerate(raw_nodes):
        if not isinstance(n, dict): raise ValueError("Each node must be an object")
        nid, typ = str(n.get("id") or f"n{i + 1}")[:40], str(n.get("type") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", nid) or nid in ids: raise ValueError(f"Node id {nid[:20]!r} is invalid or repeated")
        if typ not in BUILDER_CATALOG: raise ValueError(f"Node type {typ[:30]!r} is not available. Use one of: {', '.join(BUILDER_CATALOG)}")
        ids.add(nid)
        data = {}
        for k, v in (n.get("data") if isinstance(n.get("data"), dict) else {}).items():
            if str(k) in BUILDER_DROP_KEYS or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", str(k)): continue
            if isinstance(v, (dict, list)): v = json.dumps(v)
            if isinstance(v, bool): v = "true" if v else "false"
            if isinstance(v, (str, int, float)): data[str(k)] = str(v)[:5000]
        data["label"] = str(n.get("label") or BUILDER_CATALOG[typ][0])[:60]
        nodes.append({"id": nid, "type": typ, "position": {"x": 0, "y": 0}, "data": data})
    starts = [n for n in nodes if n["type"] in BUILDER_TRIGGERS]
    if len(starts) != 1: raise ValueError("There must be exactly one starting node (trigger, schedule or webhook)")
    edges = []
    for e in raw_edges:
        if not isinstance(e, dict) or str(e.get("source")) not in ids or str(e.get("target")) not in ids: raise ValueError("Every edge needs a source and target that are node ids")
        edge: dict[str, Any] = {"id": f"e{len(edges) + 1}", "source": str(e["source"]), "target": str(e["target"])}
        if e.get("sourceHandle"): edge["sourceHandle"] = str(e["sourceHandle"])[:20]
        edges.append(edge)
    # lay the nodes out left to right by distance from the start
    depth, queue = {starts[0]["id"]: 0}, [starts[0]["id"]]
    while queue:
        cur = queue.pop(0)
        for e in edges:
            if e["source"] == cur and e["target"] not in depth: depth[e["target"]] = depth[cur] + 1; queue.append(e["target"])
    if len(depth) != len(nodes): raise ValueError("Every node must be reachable from the starting node: " + ", ".join(n["id"] for n in nodes if n["id"] not in depth))
    rows: dict[int, int] = {}
    for n in nodes:
        d = depth[n["id"]]
        n["position"] = {"x": 60 + d * 260, "y": 80 + rows.get(d, 0) * 150}
        rows[d] = rows.get(d, 0) + 1
    wf = WorkflowIn(name=str(draft.get("name") or "AI draft")[:120], description=str(draft.get("description") or "")[:500], nodes=nodes, edges=edges, tags=["ai-draft"], settings={})
    try: validate_flow(wf)
    except HTTPException as exc: raise ValueError(str(exc.detail))
    return wf


class BuildIn(BaseModel):
    description: str = Field(min_length=10, max_length=2000)
    credential: str = Field(min_length=1, max_length=40)


@app.post("/api/ai/build-workflow")
def build_workflow_with_ai(body: BuildIn, request: Request):
    """Draft a workflow from a plain description using the caller's AI credential. The result is saved inactive and never runs by itself."""
    secrets_ = load_secrets(ALL_PROJECTS)
    token = _secrets_var.set(secrets_)
    try:
        base, model, key = ai_connection({"credential": body.credential})
        messages = [{"role": "system", "content": builder_prompt()}, {"role": "user", "content": body.description.strip()}]
        text, last = _chat(base, model, key, messages, 0.2), ""
        for attempt in range(2):
            try: wf = builder_clean(_json_reply(text)); break
            except ValueError as exc:
                last = str(exc)
                if attempt == 1: raise HTTPException(422, f"The model's draft could not be used: {last}")
                text = _chat(base, model, key, messages + [{"role": "assistant", "content": text[:6000]}, {"role": "user", "content": f"That draft was rejected: {last}. Reply again with the corrected JSON object only."}], 0.0)
    except ValueError as exc: raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc: raise HTTPException(502, f"The AI service could not be used ({type(exc).__name__}). Check the credential and that the model is running.")
    finally: _secrets_var.reset(token)
    wid = str(uuid.uuid4())
    doc = wf.model_dump()
    doc["id"] = wid
    with conn() as db: insert_workflow(db, wid, wf.name, wf.description, doc, now())
    return {"id": wid, "name": wf.name, "nodes": len(wf.nodes), "note": "Saved as an inactive draft. Check it, try a Dry run, then activate it."}


# ---------------------------------------------------------------------------
# AI Agent: a model that can call a short list of safe tools (and chosen workflows) before it answers
# ---------------------------------------------------------------------------
AGENT_TOOL_NAMES = ("calculator", "current_datetime", "fetch_url")
AGENT_MAX_STEPS = 6
AGENT_DEADLINE_SECONDS = 150
AGENT_TOOL_RESULT_LIMIT = 4000


def agent_workflows(d: dict) -> dict[str, str]:
    """Tool names (workflow_1 ...) for the workflows the agent may run, mapped to workflow ids."""
    ids = [w.strip() for w in str(d.get("workflows") or "").split(",") if w.strip()]
    if len(ids) > 5: raise ValueError("An agent can run at most 5 workflows")
    return {f"workflow_{i + 1}": wid for i, wid in enumerate(ids)}


def agent_specs(enabled: set[str], flows: dict[str, str]) -> list[dict]:
    def fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
        return {"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": required}}}
    specs = []
    if "calculator" in enabled: specs.append(fn("calculator", "Evaluate an arithmetic or text expression such as round(12.5 * 3, 2) or 'abc'.upper(). Use it for any calculation.", {"expression": {"type": "string"}}, ["expression"]))
    if "current_datetime" in enabled: specs.append(fn("current_datetime", "Get the current date and time in UTC.", {}, []))
    if "fetch_url" in enabled: specs.append(fn("fetch_url", "Download a public web page or JSON API (HTTPS only) and return its text, shortened.", {"url": {"type": "string"}}, ["url"]))
    for tool, wid in flows.items():
        try: wf = get_workflow(wid)
        except HTTPException: raise ValueError("A workflow chosen for the agent no longer exists")
        specs.append(fn(tool, f"Run the workflow '{wf['name']}'. {str(wf.get('description') or '')[:200]}".strip(), {"input": {"description": "Data to give the workflow (any JSON value)"}}, []))
    return specs


def agent_tool(name: str, args: Any, enabled: set[str], flows: dict[str, str]) -> str:
    if not isinstance(args, dict): args = {}
    if name == "calculator" and "calculator" in enabled: return json.dumps(eval_expression(str(args.get("expression", "")), {}), default=str)
    if name == "current_datetime" and "current_datetime" in enabled: return now()
    if name == "fetch_url" and "fetch_url" in enabled:
        res = do_request("GET", safe_url(str(args.get("url", ""))), {"Accept": "text/html,application/json,text/plain"})
        text = res if isinstance(res, str) else json.dumps(res, default=str)
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()[:AGENT_TOOL_RESULT_LIMIT]
    if name in flows:
        _shown, passed, _branch = step_action({"type": "subflow", "data": {"workflow_id": flows[name]}}, args.get("input"))
        return json.dumps(passed, default=str)[:AGENT_TOOL_RESULT_LIMIT]
    raise ValueError(f"The tool {name!r} is not available")


def run_agent(d: dict, previous: Any) -> Any:
    base, model, key = ai_connection(d)
    task = render_template(str(d.get("prompt", "")), previous)
    system = render_template(str(d.get("system", "")), previous)
    if not task.strip(): raise ValueError("Write the task for the agent")
    if len(task) > 50_000 or len(system) > 10_000: raise ValueError("The prompt is too long")
    enabled = {t.strip() for t in str(d.get("tools") or "").split(",") if t.strip()}
    unknown = enabled - set(AGENT_TOOL_NAMES)
    if unknown: raise ValueError(f"Unknown tool: {sorted(unknown)[0]}")
    flows = agent_workflows(d)
    specs = agent_specs(enabled, flows)
    try: limit = max(1, min(int(float(d.get("max_steps") or 4)), AGENT_MAX_STEPS))
    except (TypeError, ValueError): raise ValueError("Maximum steps must be a number")
    messages: list[dict] = ([{"role": "system", "content": system}] if system.strip() else []) + [{"role": "user", "content": task}]
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    started, steps = time.time(), []

    def ask(with_tools: bool) -> dict:
        if time.time() - started > AGENT_DEADLINE_SECONDS: raise ValueError(f"The agent ran longer than {AGENT_DEADLINE_SECONDS} seconds and was stopped")
        payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.2, "stream": False}
        if with_tools and specs: payload.update(tools=specs, tool_choice="auto")
        res = do_request("POST", base + "/chat/completions", headers, json=payload, timeout=60.0)
        try: return res["choices"][0]["message"]
        except (TypeError, KeyError, IndexError): raise ValueError("The AI service returned an unexpected reply")

    for _ in range(limit):
        msg = ask(True)
        calls = [c for c in (msg.get("tool_calls") or []) if isinstance(c, dict)][:4]
        if not calls:
            text = str(msg.get("content") or "")
            if len(text) > 200_000: raise ValueError("The AI reply is too large")
            return {"answer": text, "steps": steps, "model": model}
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = c.get("function") or {}
            name = str(fn.get("name") or "")
            try: args = json.loads(fn.get("arguments") or "{}")
            except ValueError: args = {}
            try: result = agent_tool(name, args, enabled, flows)
            except Exception as exc: result = f"Error: {str(exc)[:300]}"
            steps.append({"tool": name, "arguments": args if isinstance(args, dict) else {}, "result": result[:1000]})
            messages.append({"role": "tool", "tool_call_id": str(c.get("id") or name), "content": result[:AGENT_TOOL_RESULT_LIMIT]})
    messages.append({"role": "user", "content": "You have used all your tool calls. Give your best final answer now."})
    text = str(ask(False).get("content") or "")
    return {"answer": text[:200_000], "steps": steps, "model": model, "note": f"Stopped after {limit} steps"}


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


INBOX_ASYNC = os.getenv("FLOWPILOT_INBOX_ASYNC", "1") != "0"
_inbox_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="flowpilot-inbox")
_inbox_busy: set[str] = set()
_inbox_lock = threading.Lock()


POLL_SOURCES = ("rss", "github_issues", "json_list", "app", "folder")
# Read-only list actions of App Action services that can be watched for new items.
POLL_APP_OPS = {"stripe": ["list_customers", "list_payments", "list_invoices"], "airtable": ["list_records"], "hubspot": ["list_contacts"], "notion": ["query_database"],
                "shopify": ["list_orders", "list_products"], "supabase": ["select_rows"], "todoist": ["list_tasks"], "dropbox": ["list_folder"], "gcalendar": ["list_events"],
                "gdrive": ["list_files"], "slack": ["list_channels"]}
POLL_ID_KEYS = ("id", "_id", "number", "iid", "gid", "sid")
MAX_POLL_ITEMS = 10
POLL_SEEN_KEEP = 2000
SAMPLE_POLL_ITEM = {"id": "sample-1", "title": "Sample item", "link": "https://example.com/sample", "published": "Mon, 01 Jan 2026 09:00:00 +0000", "summary": "A test item."}


def folder_target(rel: str) -> Path:
    """The folder to watch, inside the files folder. Blank means the files folder itself."""
    rel = rel.strip().replace("\\", "/").rstrip("/")
    root = files_dir()
    if not rel: return root
    if not FILE_PATH_RE.fullmatch(rel) or ".." in rel.split("/") or "//" in rel: raise ValueError("Use a simple relative folder such as incoming/invoices")
    target = (root / rel).resolve()
    if target != root and root not in target.parents: raise ValueError("The folder must stay inside the files folder")
    return target


def folder_pattern(d: dict) -> str:
    pattern = str(d.get("pattern") or "*").strip() or "*"
    if len(pattern) > 60 or not re.fullmatch(r"[A-Za-z0-9_.*?\- ]+", pattern): raise ValueError("The file name pattern can use letters, numbers, . _ - and the wildcards * and ?")
    return pattern


def list_folder_files(d: dict) -> list[tuple[str, Any]]:
    """Files in the watched folder, oldest first. A file counts as new again when its size or modified time changes."""
    import fnmatch
    root, folder, pattern = files_dir(), folder_target(str(d.get("folder") or "")), folder_pattern(d)
    if not folder.is_dir(): return []
    found = []
    for entry in folder.iterdir():
        if entry.is_symlink() or not entry.is_file() or not fnmatch.fnmatch(entry.name, pattern): continue
        stat = entry.stat()
        found.append((stat.st_mtime_ns, entry, stat))
    found.sort(key=lambda x: (x[0], x[1].name))
    out = []
    for mtime, entry, stat in found[:200]:
        rel = entry.relative_to(root).as_posix()
        out.append((f"{rel}|{stat.st_size}|{mtime}"[:300], {"name": entry.name, "path": rel, "size": stat.st_size, "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}))
    return out


def validate_poll(d: dict) -> None:
    inbox_minutes(d)
    source = str(d.get("source") or "rss")
    if source not in POLL_SOURCES: raise ValueError(f"Source must be one of: {', '.join(POLL_SOURCES)}")
    if source == "folder":
        folder_target(str(d.get("folder") or ""))
        folder_pattern(d)
    elif source == "app":
        service, op = str(d.get("service") or "").strip(), str(d.get("operation") or "").strip()
        if service == "connector":
            with conn() as db: row = db.execute("SELECT manifest FROM connectors WHERE name=?", (str(d.get("connector") or "").strip(),)).fetchone()
            ops = json.loads(row["manifest"])["operations"] if row else {}
            if op not in ops or ops[op]["method"] != "GET": raise ValueError("Choose a connector and one of its GET actions to watch")
        elif service not in POLL_APP_OPS or op not in POLL_APP_OPS[service]: raise ValueError("Choose a service and a list action that can be watched")
        if not isinstance(read_json(str(d.get("fields") or "{}")), dict): raise ValueError("Fields must be a JSON object")
        if str(d.get("order") or "newest_first") not in ("newest_first", "oldest_first"): raise ValueError("Order must be newest_first or oldest_first")
    elif source == "github_issues":
        if not GITHUB_REPO_RE.fullmatch(str(d.get("repo") or "").strip()): raise ValueError("Repository must look like owner/name")
    else:
        url = str(d.get("url") or "").strip()
        if not url.lower().startswith("https://"): raise ValueError("Enter the HTTPS address to watch")
        if source == "json_list" and str(d.get("order") or "newest_first") not in ("newest_first", "oldest_first"): raise ValueError("Order must be newest_first or oldest_first")


def fetch_poll_items(d: dict) -> list[tuple[str, Any]]:
    """Current items as (id, item) pairs, oldest first."""
    validate_poll(d)
    source = str(d.get("source") or "rss")
    if source == "folder": return list_folder_files(d)
    if source == "app":
        res = app_action({"service": d.get("service"), "connector": d.get("connector"), "operation": d.get("operation"), "credential": d.get("credential"), "fields": d.get("fields") or "{}"}, None)
        path = str(d.get("items_path") or "").strip()
        res = get_path(res, path) if path else res
        if not isinstance(res, list): raise ValueError("That action did not return a list" + (f" at {path!r}" if path else ""))
        id_path, pairs = str(d.get("id_path") or "").strip(), []
        for it in res[:200]:
            key = get_path(it, id_path) if id_path else next((it[k] for k in POLL_ID_KEYS if isinstance(it, dict) and it.get(k) not in (None, "")), None)
            if key is None and not id_path: key = hashlib.sha256(json.dumps(it, sort_keys=True, default=str).encode()).hexdigest()
            if key is None or key == "": continue
            pairs.append((str(key)[:300], it))
        return pairs if str(d.get("order") or "newest_first") == "oldest_first" else list(reversed(pairs))
    cred = str(d.get("credential") or "").strip()
    headers = {"Authorization": f"Bearer {lookup_secret(cred)}"} if cred else {}
    if source == "rss":
        items = read_rss({"url": str(d.get("url")).strip(), "limit": 50}, None)
        pairs = [(str(it.get("link") or it.get("title") or "")[:300], it) for it in items]
        return [p for p in reversed(pairs) if p[0]]
    if source == "github_issues":
        headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "flowpilot"})
        state = str(d.get("state") or "open")
        if state not in ("open", "closed", "all"): raise ValueError("State must be open, closed or all")
        res = do_request("GET", f"https://api.github.com/repos/{str(d.get('repo')).strip()}/issues?state={state}&per_page=30&sort=created&direction=desc", headers)
        if not isinstance(res, list): raise ValueError("Unexpected response from GitHub")
        pairs = [(str(i.get("number")), {"number": i.get("number"), "title": i.get("title"), "state": i.get("state"), "url": i.get("html_url"), "user": (i.get("user") or {}).get("login"),
                                         "body": str(i.get("body") or "")[:2000], "created_at": i.get("created_at"), "labels": [l.get("name") for l in i.get("labels") or [] if isinstance(l, dict)]})
                 for i in res if "pull_request" not in i]
        return list(reversed(pairs))
    res = do_request("GET", safe_url(str(d.get("url")).strip()), headers)
    path = str(d.get("items_path") or "").strip()
    items = get_path(res, path) if path else res
    if not isinstance(items, list): raise ValueError("The address did not return a list" + (f" at {path!r}" if path else ""))
    id_path, pairs = str(d.get("id_path") or "").strip(), []
    for it in items[:200]:
        key = get_path(it, id_path) if id_path else hashlib.sha256(json.dumps(it, sort_keys=True, default=str).encode()).hexdigest()
        if key is None or key == "": continue
        pairs.append((str(key)[:300], it))
    return pairs if str(d.get("order") or "newest_first") == "oldest_first" else list(reversed(pairs))


def reset_poll_state(workflow_id: str) -> None:
    """Forget what was seen, so the next check records what exists now without running the workflow for it."""
    with conn() as db:
        db.execute("DELETE FROM poll_seen WHERE workflow_id=?", (workflow_id,))
        db.execute("DELETE FROM poll_state WHERE workflow_id=?", (workflow_id,))


def poll_items(doc: dict, node: dict) -> None:
    secrets_ = load_secrets(workflow_project(doc))
    token = _secrets_var.set(secrets_)
    try: pairs = fetch_poll_items(node.get("data", {}) or {})
    except Exception as exc:
        err = str(redact({"e": str(exc)}, secrets_)["e"])
        def boom(): raise ValueError(err)
        execute_workflow(doc, "poll_in", boom)  # recorded as a failed run, so the problem is visible
        return
    finally: _secrets_var.reset(token)
    wid = doc["id"]
    with conn() as db:
        started = db.execute("SELECT initialized FROM poll_state WHERE workflow_id=?", (wid,)).fetchone()
        seen = {r["item_id"] for r in db.execute("SELECT item_id FROM poll_seen WHERE workflow_id=?", (wid,)).fetchall()}
        fresh = [p for p in pairs if p[0] not in seen]
        batch = [] if not started else fresh[:MAX_POLL_ITEMS]  # the first check only records what already exists
        stamp = now()
        for item_id, _ in (fresh if not started else batch): db.execute("INSERT OR IGNORE INTO poll_seen (workflow_id,item_id,seen_at) VALUES (?,?,?)", (wid, item_id, stamp))
        db.execute("INSERT INTO poll_state (workflow_id, initialized) VALUES (?, 1) ON CONFLICT(workflow_id) DO UPDATE SET initialized=1", (wid,))
        db.execute("DELETE FROM poll_seen WHERE workflow_id=? AND item_id NOT IN (SELECT item_id FROM poll_seen WHERE workflow_id=? ORDER BY seen_at DESC, item_id DESC LIMIT ?)", (wid, wid, POLL_SEEN_KEEP))
    for _, item in batch: dispatch(doc, "poll_in", item)


def submit_inbox_poll(doc: dict, node: dict) -> None:
    """Check an inbox in the background, so a slow mail server never delays other schedules. One check per workflow at a time."""
    with _inbox_lock:
        if doc["id"] in _inbox_busy: return
        _inbox_busy.add(doc["id"])

    def work() -> None:
        try: (poll_items if node.get("type") == "poll_in" else poll_inbox)(doc, node)
        except Exception: log.exception("Inbox check failed for %s", doc["id"])
        finally:
            with _inbox_lock: _inbox_busy.discard(doc["id"])

    if INBOX_ASYNC: _inbox_pool.submit(work)
    else: work()


def poll_inbox(doc: dict, node: dict) -> None:
    secrets_ = load_secrets(workflow_project(doc))
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
PARALLEL_TYPES = {"app", "http", "sql", "rss", "notify", "email", "telegram", "github", "sheets", "gmail", "ai", "code", "delay", "file"}


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


# ---------------------------------------------------------------------------
# Dry run: run the whole workflow, but anything that would send a message, change data or wait is only described.
# ---------------------------------------------------------------------------
_dry_var: contextvars.ContextVar[list | None] = contextvars.ContextVar("fp_dry", default=None)
DRY_READ_PREFIXES = ("list_", "get_", "find_", "select_", "query_", "search_", "read_", "lookup_")
DRY_HIDE_KEYS = {"credential", "auth_credential", "pin", "label", "each_item", "on_error", "retries", "retry_wait", "auth_type", "auth_header", "sample"}


def dry_fields(d: dict, previous: Any) -> dict:
    """The node's own text settings with {{placeholders}} filled in, so a dry run shows what would have gone out."""
    out = {}
    for k, v in d.items():
        if k in DRY_HIDE_KEYS or not isinstance(v, (str, int, float)) or isinstance(v, bool) or str(v) == "": continue
        try: out[k] = render_template(str(v), previous)[:500]
        except Exception: out[k] = str(v)[:500]
    return out


def dry_preview(typ: str, d: dict, previous: Any) -> dict | None:
    """None when the node is safe to really run (it only reads or computes); otherwise a description of what it would have done."""
    def would(what: str, **extra) -> dict: return {"dry_run": True, "not_done": what, **extra, "settings": dry_fields(d, previous)}
    op = str(d.get("operation") or "")
    if typ in ("delay", "wait"): return {"dry_run": True, "not_done": "wait", "skipped": "the pause was skipped"}
    if typ == "callback": return {"dry_run": True, "not_done": "wait for a response", "skipped": "continued without waiting"}
    if typ in ("email", "notify", "telegram", "gmail"): return would("send a message")
    if typ == "http":
        method = str(d.get("method", "GET")).upper()
        return None if method == "GET" else would(f"{method} request")
    if typ == "file": return None if op in ("", "read", "list") else would(f"file {op}")
    if typ == "table": return None if op in ("find", "get", "count", "") else would(f"table {op}")
    if typ == "sql":
        first = re.sub(r"^[\s(]+", "", str(d.get("query") or ""))[:8].lower()
        return None if first.startswith(("select", "with", "explain")) else would("SQL change")
    if typ == "sheets": return None if op in ("", "read") else would(f"Google Sheets {op}")
    if typ == "github": return None if op == "list_issues" else would("GitHub change")
    if typ == "app":
        if str(d.get("service") or "") == "connector":
            with conn() as db: row = db.execute("SELECT manifest FROM connectors WHERE name=?", (str(d.get("connector") or ""),)).fetchone()
            method = (json.loads(row["manifest"])["operations"].get(op, {}).get("method", "POST") if row else "POST")
            return None if method == "GET" else would(f"connector call ({method})")
        return None if op.startswith(DRY_READ_PREFIXES) else would(f"{d.get('service') or 'app'} {op}")
    return None


def step_action(node: dict, previous: Any):
    """Run one node. Returns (displayed_output, value_passed_downstream, branch)."""
    typ = node["type"]
    d = node.get("data", {}) or {}
    if typ in START_TYPES or typ in ("note", "output"): return previous, previous, None
    if _dry_var.get() is not None:
        preview = dry_preview(typ, d, previous)
        if preview is not None:
            _dry_var.get().append({"node": node.get("id"), "type": typ, "not_done": preview["not_done"]})
            return preview, previous, None
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
    if typ in ITEM_TOOLS:
        value = ITEM_TOOLS[typ](d, previous)
        return value, value, None
    if typ == "agent":
        value = run_agent(d, previous)
        return value, value, None
    if typ == "app":
        value = app_action(d, previous)
        return value, value, None
    if typ == "callback": raise ValueError("Wait for Response must be run by the engine")
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
            if str(d.get("body_type") or "auto") == "form":
                form = read_json(as_json)
                if not isinstance(form, dict): raise ValueError("A form body must be a JSON object, for example {\"name\": \"{{name}}\"}")
                kwargs["data"] = {str(k): param_text(v) for k, v in form.items()}
            else:
                try: kwargs["json"] = json.loads(as_json)
                except ValueError:
                    kwargs["content"] = render_template(raw_body, previous).encode()
                    if not any(k.lower() == "content-type" for k in headers): headers["Content-Type"] = "text/plain; charset=utf-8"
        query_text = str(d.get("query") or "").strip()
        if query_text:
            query = read_json(render_template(query_text, previous, json_escape=True))
            if not isinstance(query, dict) or len(query) > 30: raise ValueError("Query parameters must be a JSON object with at most 30 entries, for example {\"page\": 1}")
            kwargs["params"] = {str(k): param_text(v) for k, v in query.items()}
        try: kwargs["timeout"] = max(1.0, min(float(d.get("timeout") or 8) or 8.0, 60.0))
        except (TypeError, ValueError): raise ValueError("Timeout must be a number of seconds")
        save_as = render_template(str(d.get("save_as") or ""), previous).strip()
        if save_as:
            got_file = do_request(method, url, headers, raw=True, **kwargs)
            target = file_target(save_as)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(got_file["bytes"])
            value = {"path": save_as, "size": len(got_file["bytes"]), "content_type": got_file["content_type"]}
            return value, value, None
        if str(d.get("paginate") or "none") != "none":
            value = paginate_http(method, url, headers, kwargs, d)
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
        live = _live_var.get()
        if live is not None and live["steps"] is None: live["steps"] = self.steps
        self.suspended: dict | None = None
        timeout = (doc.get("settings") or {}).get("timeout_seconds")
        self.limit = int(timeout) if timeout else 0
        self.deadline = time.monotonic() + self.limit if self.limit else None

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


EACH_ITEM_TYPES = {"agent", "datetime", "crypto", "convert", "table", "app", "transform", "template", "set", "code", "http", "ai", "sql", "notify", "email", "telegram", "gmail", "github", "sheets", "file", "rss"}
MAX_EACH_ITEMS = 200


def step_each_item(node: dict, value: Any):
    """Run a node once for every element of a list (like n8n's item-by-item mode). The results come back as a list in the same order."""
    if not isinstance(value, list): raise ValueError("'Run once for each item' needs a list as input")
    if len(value) > MAX_EACH_ITEMS: raise ValueError(f"'Run once for each item' is limited to {MAX_EACH_ITEMS} items")
    shown, passed = [], []
    for i, item in enumerate(value):
        try: out, keep, _branch = step_action(node, item)
        except Exception as exc: raise ValueError(f"Item {i + 1} of {len(value)}: {exc}") from exc
        shown.append(out)
        passed.append(keep)
    return shown, passed, None


MAX_STEP_TIMEOUT = 300


def node_timeout(data: dict, typ: str) -> int | None:
    raw = data.get("step_timeout")
    if typ not in ERROR_SETTINGS or raw in (None, "", 0, "0"): return None
    try: secs = int(float(raw))
    except (TypeError, ValueError): raise ValueError("The step time limit must be a number of seconds")
    if not 1 <= secs <= MAX_STEP_TIMEOUT: raise ValueError(f"The step time limit must be between 1 and {MAX_STEP_TIMEOUT} seconds")
    return secs


def with_step_timeout(limit: int | None, call):
    """Give up waiting for one step after `limit` seconds. The step itself cannot be stopped from outside, so a request already sent may still finish."""
    if not limit: return call()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flowpilot-step")
    future = pool.submit(contextvars.copy_context().run, call)
    try: return future.result(timeout=limit)
    except FutureTimeout: raise ValueError(f"This step took longer than its limit of {limit} seconds")
    finally: pool.shutdown(wait=False)


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
                    each = typ in EACH_ITEM_TYPES and str(data.get("each_item") or "false") == "true"
                    shown, passed, branch = with_step_timeout(node_timeout(data, typ), lambda: step_each_item(node, value) if each else step_action(node, value))
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


def suspend_for_callback(g: Graph, nid: str, data: dict, value: Any, step: dict) -> bool:
    """A Wait for Response node: pause the run until someone answers through a link, a button or the API (or the time limit passes)."""
    try: secs = wait_seconds({"seconds": data.get("seconds") or 1, "unit": data.get("unit") or "days"})
    except ValueError as exc:
        step.update(status="error", error=str(exc))
        raise StepFailure(str(exc)) from exc
    if secs < 60:
        step.update(status="error", error="The time limit of a Wait for Response must be at least one minute")
        raise StepFailure(step["error"])
    if g.iter or len(_stack_var.get()) > 1:
        step.update(status="error", error="Wait for Response cannot be used inside a loop or a sub-workflow")
        raise StepFailure(step["error"])
    if g.suspended:
        step.update(status="error", error="Only one long Wait or Wait for Response per run is supported")
        raise StepFailure(step["error"])
    if not _run_var.get():
        step.update(status="error", error="Wait for Response needs a saved run")
        raise StepFailure(step["error"])
    on_timeout = "fail" if str(data.get("on_timeout") or "continue") == "fail" else "continue"
    resume_at = (datetime.now(timezone.utc) + timedelta(seconds=secs)).isoformat()
    g.suspended = {"node_id": nid, "value": {"data": value, "on_timeout": on_timeout}, "resume_at": resume_at, "kind": "callback"}
    step.update(status="waiting", output={"waiting_for": "a response", "expires_at": resume_at, **{k: v for k, v in _run_var.get().items() if k != "resume_url"}}, duration_ms=0)
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
        if g.deadline is not None and time.monotonic() > g.deadline: raise StepFailure(f"The workflow ran longer than its time limit of {g.limit} seconds")
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
        if _dry_var.get() is None and len(prepared) == 1 and prepared[0][2] == "wait" and suspend_for_long_wait(g, prepared[0][0], prepared[0][3], prepared[0][4], prepared[0][5]): continue
        if _dry_var.get() is None and len(prepared) == 1 and prepared[0][2] == "callback" and suspend_for_callback(g, prepared[0][0], prepared[0][3], prepared[0][4], prepared[0][5]): continue
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
            nid, value, *flags = resume
            if nid not in g.nodes: raise ValueError("The waiting node no longer exists in this workflow" if not flags else "The failed node no longer exists in this workflow")
            if flags:  # replay: run the node itself again, then everything after it
                allowed = g.reach([nid])
                if g.has_cycle(allowed): raise ValueError("Cycles are not supported in this release")
                final = run_graph(g, allowed, [({"target": nid}, value)])
            else:
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


def load_variables() -> dict:
    with conn() as db: return {r["name"]: r["value"] for r in db.execute("SELECT name, value FROM variables").fetchall()}


# ---------------------------------------------------------------------------
# Data Tables browser (read for everyone, changes for editors and above through the default role rules)
# ---------------------------------------------------------------------------
def _table_or_404(name: str) -> str:
    if not TABLE_NAME_RE.fullmatch(name): raise HTTPException(404, "Table not found")
    return name


def check_table_project(request: Request, project_id: str | None) -> None:
    actor = getattr(request.state, "actor", None)
    if project_id is None or sees_everything(actor): return
    if project_id not in my_project_ids(actor): raise HTTPException(404, "Table not found")


@app.get("/api/tables")
def list_tables(request: Request):
    actor = getattr(request.state, "actor", None)
    with conn() as db:
        rows = db.execute("SELECT tbl AS name, project_id, COUNT(*) AS rows, MAX(created_at) AS last_added FROM data_rows GROUP BY tbl, project_id ORDER BY tbl").fetchall()
        names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM projects").fetchall()}
    allowed_projects = None if sees_everything(actor) else my_project_ids(actor)
    return [{**dict(r), "project_name": names.get(r["project_id"])} for r in rows if allowed_projects is None or r["project_id"] is None or r["project_id"] in allowed_projects]


@app.get("/api/tables/{name}")
def read_table(name: str, request: Request, project_id: str | None = None, limit: int = 200):
    name = _table_or_404(name)
    check_table_project(request, project_id)
    sc, sp = table_scope(project_id)
    with conn() as db:
        total = db.execute(f"SELECT COUNT(*) AS n FROM data_rows WHERE tbl=? AND {sc}", (name, *sp)).fetchone()["n"]
        if not total: raise HTTPException(404, "Table not found")
        rows = db.execute(f"SELECT id, data, created_at FROM data_rows WHERE tbl=? AND {sc} ORDER BY id DESC LIMIT ?", (name, *sp, max(1, min(limit, 1000)))).fetchall()
    return {"name": name, "project_id": project_id, "total": total, "rows": [{"_id": r["id"], "added": r["created_at"], **json.loads(r["data"])} for r in rows]}


@app.delete("/api/tables/{name}/rows/{row_id}")
def delete_table_row(name: str, row_id: int, request: Request, project_id: str | None = None):
    name = _table_or_404(name)
    check_table_project(request, project_id)
    sc, sp = table_scope(project_id)
    with conn() as db:
        if not db.execute(f"DELETE FROM data_rows WHERE tbl=? AND id=? AND {sc}", (name, row_id, *sp)).rowcount: raise HTTPException(404, "Row not found")
    return {"deleted": 1}


@app.delete("/api/tables/{name}")
def clear_table(name: str, request: Request, project_id: str | None = None):
    name = _table_or_404(name)
    check_table_project(request, project_id)
    sc, sp = table_scope(project_id)
    with conn() as db: n = db.execute(f"DELETE FROM data_rows WHERE tbl=? AND {sc}", (name, *sp)).rowcount
    if not n: raise HTTPException(404, "Table not found")
    return {"deleted": n}


MAX_VARIABLES = 200


class VariableIn(BaseModel):
    value: str = Field(max_length=2000)


@app.get("/api/variables")
def list_variables():
    with conn() as db: rows = db.execute("SELECT name, value, updated_at FROM variables ORDER BY name").fetchall()
    return [dict(r) for r in rows]


MAX_CONNECTORS = 30


class ConnectorIn(BaseModel):
    manifest: dict


def connector_summary(name: str, manifest: dict) -> dict:
    return {"name": name, "label": manifest["label"], "base_url": manifest["base_url"], "auth": manifest["auth"],
            "operations": {k: {"label": o["label"], "method": o["method"], "example": o.get("example", {}), "required": o["required"]} for k, o in manifest["operations"].items()}}


@app.get("/api/connectors")
def list_connectors():
    with conn() as db: rows = db.execute("SELECT name, manifest FROM connectors ORDER BY name").fetchall()
    return [connector_summary(r["name"], json.loads(r["manifest"])) for r in rows]


@app.get("/api/connector-library")
def connector_library_list():
    """Ready-made manifests, never run against the live services. Adding one is just saving it as a connector."""
    return [{"id": e["id"], "label": e["manifest"]["label"], "about": e["about"], "manifest": e["manifest"]} for e in CONNECTOR_LIBRARY]


@app.get("/api/connectors/{name}")
def get_connector(name: str):
    with conn() as db: row = db.execute("SELECT manifest FROM connectors WHERE name=?", (name,)).fetchone()
    if not row: raise HTTPException(404, "Connector not found")
    return json.loads(row["manifest"])


@app.put("/api/connectors/{name}")
def put_connector(name: str, body: ConnectorIn):
    if not connector_lib.NAME_RE.fullmatch(name): raise HTTPException(400, "Name uses lowercase letters, numbers, _ and - (max 40) and starts with a letter")
    try: manifest = connector_lib.validate_manifest(body.manifest)
    except ValueError as exc: raise HTTPException(400, str(exc))
    with conn() as db:
        if not db.execute("SELECT 1 FROM connectors WHERE name=?", (name,)).fetchone() and db.execute("SELECT COUNT(*) c FROM connectors").fetchone()["c"] >= MAX_CONNECTORS:
            raise HTTPException(400, f"At most {MAX_CONNECTORS} connectors")
        db.execute("INSERT INTO connectors (name,manifest,updated_at) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET manifest=excluded.manifest, updated_at=excluded.updated_at", (name, json.dumps(manifest), now()))
    return connector_summary(name, manifest)


@app.delete("/api/connectors/{name}", status_code=204)
def delete_connector(name: str):
    with conn() as db: db.execute("DELETE FROM connectors WHERE name=?", (name,))


@app.put("/api/variables/{name}")
def put_variable(name: str, body: VariableIn):
    if not NAME_RE.fullmatch(name): raise HTTPException(400, "Name must start with a letter and use only letters, numbers, _ and - (max 40)")
    with conn() as db:
        if not db.execute("SELECT 1 FROM variables WHERE name=?", (name,)).fetchone() and db.execute("SELECT COUNT(*) c FROM variables").fetchone()["c"] >= MAX_VARIABLES:
            raise HTTPException(400, f"At most {MAX_VARIABLES} variables")
        db.execute("INSERT INTO variables (name,value,updated_at) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (name, body.value, now()))
    return {"name": name, "saved": True}


@app.delete("/api/variables/{name}", status_code=204)
def delete_variable(name: str):
    with conn() as db: db.execute("DELETE FROM variables WHERE name=?", (name,))


def callback_secret() -> bytes:
    with conn() as db:
        row = db.execute("SELECT value FROM settings WHERE key='callback_secret'").fetchone()
        if row: return row["value"].encode()
        value = secrets.token_hex(32)
        db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('callback_secret', ?)", (value,))
        return db.execute("SELECT value FROM settings WHERE key='callback_secret'").fetchone()["value"].encode()


def callback_token(exec_id: str) -> str:
    return f"{exec_id}.{hmac.new(callback_secret(), exec_id.encode(), hashlib.sha256).hexdigest()[:32]}"


def callback_exec_id(token: str) -> str | None:
    exec_id, _, sig = token.rpartition(".")
    good = hmac.new(callback_secret(), exec_id.encode(), hashlib.sha256).hexdigest()[:32] if exec_id else ""
    return exec_id if exec_id and hmac.compare_digest(sig.encode(), good.encode()) else None


def run_links(exec_id: str | None) -> dict:
    if not exec_id: return {}
    url = f"{public_url()}/api/resume/{callback_token(exec_id)}"
    return {"resume_url": url, "approve_url": url + "?decision=approve", "reject_url": url + "?decision=reject"}


def run_flow(doc: dict, initial: Any = None, resume: tuple | None = None, exec_id: str | None = None) -> dict:
    """Run a workflow with credentials available, and hide their values from the result."""
    secrets_ = load_secrets(workflow_project(doc))
    live = _live_var.get()
    if live is not None: live["secrets"] = secrets_
    holder: dict = {}
    t1, t2, t3, t4, t5 = _secrets_var.set(secrets_), _stack_var.set((doc.get("id"),)), _response_var.set(holder), _vars_var.set(load_variables()), _run_var.set(run_links(exec_id))
    limit, token_limit = (doc.get("settings") or {}).get("ai_call_limit"), (doc.get("settings") or {}).get("ai_token_limit")
    budget = {"limit": int(limit) if limit else None, "used": 0, "token_limit": int(token_limit) if token_limit else None, "tokens": 0}
    own_budget = _ai_budget_var.get() is None  # a sub-workflow shares its caller's budget and usage count
    t6 = _ai_budget_var.set(budget) if own_budget else None
    try: result = _execute(doc, initial, resume)
    finally:
        if t6 is not None: _ai_budget_var.reset(t6)
        _run_var.reset(t5)
        _vars_var.reset(t4)
        _secrets_var.reset(t1)
        _stack_var.reset(t2)
        _response_var.reset(t3)
    if result["status"] == "success" and "response" in holder: result["response"] = holder["response"]
    if own_budget and budget["used"]: result["ai_usage"] = {"calls": budget["used"], "tokens": budget["tokens"]}
    return redact(result, secrets_)


def execute_workflow(doc: dict, kind: str, initial: Any = None, exec_id: str | None = None) -> dict:
    """Run a workflow and store the execution. `initial` may be a callable so input errors are recorded as failed runs."""
    exec_id, started = exec_id or str(uuid.uuid4()), now()
    try: result = run_flow(doc, initial() if callable(initial) else initial, exec_id=exec_id)
    except Exception as exc: result = {"status": "failed", "output": None, "steps": [], "error": str(exc)}
    policy = (doc.get("settings") or {}).get("save_runs", "all")
    skip_save = kind not in ("manual", "replay", "dry") and result["status"] != "waiting" and (policy == "none" or (policy == "failures" and result["status"] == "success"))
    with conn() as db:
        if result["status"] == "waiting" and db.execute("SELECT COUNT(*) c FROM waits WHERE status='pending'").fetchone()["c"] >= MAX_PENDING_WAITS:
            result = {"status": "failed", "output": None, "steps": result["steps"], "error": f"Too many runs are waiting (limit {MAX_PENDING_WAITS})"}
        if not skip_save: db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind,ai_usage) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (exec_id, doc["id"], result["status"], started, None if result["status"] == "waiting" else now(), json.dumps(result["output"]), json.dumps(result["steps"]), result["error"], kind,
                    json.dumps(result["ai_usage"]) if result.get("ai_usage") else None))
        if result["status"] == "waiting" and not skip_save: add_wait(db, exec_id, doc["id"], result["wait"])
    if result["status"] == "failed" and kind not in ("error", "dry"): run_error_handler(doc, exec_id, kind, result)
    return {"id": exec_id, "workflow_id": doc["id"], "trigger": kind, **result}


def add_wait(db, exec_id: str, workflow_id: str, wait: dict) -> None:
    db.execute("INSERT INTO waits (id,execution_id,workflow_id,node_id,value,resume_at,status,created_at,kind) VALUES (?,?,?,?,?,?,'pending',?,?)",
               (str(uuid.uuid4()), exec_id, workflow_id, wait["node_id"], json.dumps(wait["value"], default=str), wait["resume_at"], now(), wait.get("kind", "timer")))


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


def finish_wait(w, ex, answer: dict | None = None) -> None:
    """Continue a waiting run. `answer` is {"decision", "body"} for a Wait for Response that was answered; without it a response wait has timed out."""
    old_steps = json.loads(ex["steps"] or "[]")
    stored = json.loads(w["value"])
    resume_value, decision = stored, None
    if w["kind"] == "callback":
        decision = answer["decision"] if answer else "timeout"
        resume_value = {"decision": decision, "data": stored.get("data"), "body": (answer or {}).get("body")}
        if answer is None and stored.get("on_timeout") == "fail":
            with conn() as db:
                db.execute("UPDATE executions SET status='failed', finished_at=?, error='No response arrived in time' WHERE id=?", (now(), ex["id"]))
                db.execute("UPDATE waits SET status='done' WHERE id=?", (w["id"],))
            return
    for st in old_steps:
        if st.get("node_id") == w["node_id"] and st.get("status") == "waiting":
            st.update(status="success", output={"resumed_at": now(), **({"decision": decision} if decision else {})})
    try:
        doc = get_workflow(w["workflow_id"])
        result = run_flow(doc, None, resume=(w["node_id"], resume_value), exec_id=ex["id"])
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


class ReplayIn(BaseModel):
    input: Any = None  # optional: replace the data the failed node received (any JSON)


def _set_in_doc_size(value: Any) -> None:
    if len(json.dumps(value, default=str)) > 200_000: raise HTTPException(400, "The edited input is too large (200 KB at most)")


@app.post("/api/executions/{execution_id}/replay")
def replay_execution(execution_id: str, body: ReplayIn | None = None):
    """Run a failed execution again from the node that failed, using the data that node received (or data the caller edited)."""
    with conn() as db: ex = db.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
    if not ex: raise HTTPException(404, "Execution not found")
    if ex["status"] != "failed": raise HTTPException(400, "Only failed runs can be replayed")
    steps = json.loads(ex["steps"] or "[]")
    failed = next((s for s in reversed(steps) if s.get("status") == "error" and not s.get("continued")), None)
    if not failed: raise HTTPException(400, "This run did not stop at a node, so there is nothing to replay")
    if failed.get("iteration"): raise HTTPException(400, "The failure happened inside a loop, which cannot be replayed from the middle")
    if failed.get("type") == "merge": raise HTTPException(400, "A failed Merge node cannot be replayed")
    edited = body is not None and "input" in body.model_fields_set
    value = failed.get("input")
    if edited:
        value = body.input
        _set_in_doc_size(value)
    elif isinstance(value, dict) and value.get("truncated") is True and "preview" in value: raise HTTPException(400, "The input of the failed node was too large to keep, so it cannot be replayed")
    try: doc = get_workflow(ex["workflow_id"])
    except HTTPException: raise HTTPException(404, "The workflow no longer exists")
    kept = [s for s in steps if s is not failed and s.get("status") in ("success", "skipped")]
    new_id = str(uuid.uuid4())
    try: result = run_flow(doc, None, resume=(failed["node_id"], value, True), exec_id=new_id)
    except Exception as exc: result = {"status": "failed", "output": None, "steps": [], "error": str(exc)}
    all_steps = kept + result["steps"]
    with conn() as db:
        db.execute("INSERT INTO executions (id,workflow_id,status,started_at,finished_at,output,steps,error,trigger_kind,replay_of,ai_usage) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (new_id, doc["id"], result["status"], now(), None if result["status"] == "waiting" else now(), json.dumps(result["output"]), json.dumps(all_steps), result["error"], "replay", execution_id,
                    json.dumps(result["ai_usage"]) if result.get("ai_usage") else None))
        if result["status"] == "waiting": add_wait(db, new_id, doc["id"], result["wait"])
    if result["status"] == "failed": run_error_handler(doc, new_id, "replay", {**result, "steps": all_steps})
    return {"id": new_id, "workflow_id": doc["id"], "trigger": "replay", "replayed_from": execution_id, "edited_input": edited, **{**result, "steps": all_steps}}


class BulkReplayIn(BaseModel):
    workflow_id: str | None = None
    since_minutes: int = Field(60, ge=1, le=10080)
    limit: int = Field(25, ge=1, le=100)


@app.post("/api/executions/replay-failed")
def replay_failed(body: BulkReplayIn, request: Request):
    """Replay recent failed runs from their failed step, once each. Runs that were already replayed are left alone."""
    visible = visible_workflow_ids(getattr(request.state, "actor", None))
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=body.since_minutes)).isoformat()
    sql, args = "SELECT id, workflow_id FROM executions WHERE status='failed' AND started_at >= ? AND trigger_kind NOT IN ('dry','error') AND id NOT IN (SELECT replay_of FROM executions WHERE replay_of IS NOT NULL)", [cutoff]
    if body.workflow_id: sql, args = sql + " AND workflow_id=?", args + [body.workflow_id]
    with conn() as db: rows = db.execute(sql + " ORDER BY started_at", args).fetchall()
    rows = [r for r in rows if visible is None or r["workflow_id"] in visible][:body.limit]
    results = []
    for r in rows:
        try:
            res = replay_execution(r["id"])
            results.append({"id": r["id"], "new_id": res["id"], "workflow_id": r["workflow_id"], "status": res["status"], "error": res.get("error")})
        except HTTPException as exc: results.append({"id": r["id"], "workflow_id": r["workflow_id"], "status": "skipped", "error": str(exc.detail)})
    return {"found": len(rows), "succeeded": sum(r["status"] == "success" for r in results), "still_failing": sum(r["status"] in ("failed", "waiting") for r in results),
            "skipped": sum(r["status"] == "skipped" for r in results), "results": results}


MAX_TESTS = 50


class TestCaseIn(BaseModel):
    __test__ = False  # not a pytest class
    name: str = Field(min_length=1, max_length=80)
    input: Any = None
    expected: Any = None
    mode: Literal["contains", "equals"] = "contains"
    expect: Literal["success", "failed"] = "success"


def _test_row(r) -> dict:
    spec = json.loads(r["spec"])
    return {"id": r["id"], "name": r["name"], "created_at": r["created_at"], **spec}


@app.get("/api/workflows/{workflow_id}/tests")
def list_tests(workflow_id: str):
    get_workflow(workflow_id)
    with conn() as db: rows = db.execute("SELECT * FROM workflow_tests WHERE workflow_id=? ORDER BY created_at, id", (workflow_id,)).fetchall()
    return [_test_row(r) for r in rows]


@app.post("/api/workflows/{workflow_id}/tests", status_code=201)
def add_test(workflow_id: str, body: TestCaseIn):
    get_workflow(workflow_id)
    spec = {"input": body.input, "expected": body.expected, "mode": body.mode, "expect": body.expect}
    if len(json.dumps(spec, default=str)) > 100_000: raise HTTPException(400, "The test is too large (100 KB at most)")
    tid = str(uuid.uuid4())
    with conn() as db:
        if db.execute("SELECT COUNT(*) c FROM workflow_tests WHERE workflow_id=?", (workflow_id,)).fetchone()["c"] >= MAX_TESTS: raise HTTPException(400, f"At most {MAX_TESTS} tests per workflow")
        if db.execute("SELECT 1 FROM workflow_tests WHERE workflow_id=? AND name=?", (workflow_id, body.name)).fetchone(): raise HTTPException(400, "A test with this name already exists")
        db.execute("INSERT INTO workflow_tests (id,workflow_id,name,spec,created_at) VALUES (?,?,?,?,?)", (tid, workflow_id, body.name, json.dumps(spec, default=str), now()))
    return {"id": tid, "name": body.name, **spec}


@app.delete("/api/workflows/{workflow_id}/tests/{test_id}", status_code=204)
def delete_test(workflow_id: str, test_id: str):
    with conn() as db: db.execute("DELETE FROM workflow_tests WHERE id=? AND workflow_id=?", (test_id, workflow_id))


def contains(expected: Any, actual: Any) -> bool:
    """Every field in `expected` is present in `actual` with the same value (extra fields in `actual` are fine)."""
    if isinstance(expected, dict): return isinstance(actual, dict) and all(k in actual and contains(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list): return isinstance(actual, list) and len(expected) == len(actual) and all(contains(e, a) for e, a in zip(expected, actual))
    return expected == actual


@app.post("/api/workflows/{workflow_id}/tests/run")
def run_tests(workflow_id: str):
    """Run every saved test as a dry run (writes and messages are described, never done) and compare the outcome with what was expected."""
    doc = get_workflow(workflow_id)
    with conn() as db: rows = db.execute("SELECT * FROM workflow_tests WHERE workflow_id=? ORDER BY created_at, id", (workflow_id,)).fetchall()
    results = []
    for r in rows:
        case = _test_row(r)
        token, t0 = _dry_var.set([]), time.perf_counter()
        try: result = run_flow(doc, case["input"])
        except Exception as exc: result = {"status": "failed", "output": None, "error": str(exc), "steps": []}
        finally: _dry_var.reset(token)
        if case["expect"] == "failed":
            ok = result["status"] == "failed" and (case["expected"] in (None, "") or str(case["expected"]).lower() in str(result.get("error") or "").lower())
        elif result["status"] != "success": ok = False
        elif case["expected"] is None: ok = True
        else: ok = contains(case["expected"], result["output"]) if case["mode"] == "contains" else case["expected"] == result["output"]
        shown = result["output"] if case["expect"] == "success" else result.get("error")
        text = json.dumps(shown, default=str)
        results.append({"id": case["id"], "name": case["name"], "passed": bool(ok), "status": result["status"], "error": result.get("error"),
                        "actual": shown if len(text) <= 3000 else text[:3000] + "...(shortened)", "ms": round((time.perf_counter() - t0) * 1000, 1)})
    return {"passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results), "results": results}


class ExplainIn(BaseModel):
    credential: str = Field(min_length=1, max_length=40)


@app.post("/api/executions/{execution_id}/explain")
def explain_execution(execution_id: str, body: ExplainIn):
    """Ask the caller's AI credential to explain why a run failed and what to change. Credentials are removed from everything the model sees."""
    with conn() as db: ex = db.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
    if not ex: raise HTTPException(404, "Execution not found")
    if ex["status"] != "failed": raise HTTPException(400, "Only failed runs can be explained")
    steps = json.loads(ex["steps"] or "[]")
    failed = next((s for s in reversed(steps) if s.get("status") == "error" and not s.get("continued")), None)
    try: doc = get_workflow(ex["workflow_id"])
    except HTTPException: doc = None
    node = next((n for n in (doc or {}).get("nodes", []) if failed and n["id"] == failed.get("node_id")), None)
    settings = {k: v for k, v in ((node or {}).get("data") or {}).items() if k not in BUILDER_DROP_KEYS and k != "code"} if node else {}
    facts = {"workflow": (doc or {}).get("name"), "error": ex["error"], "failed_node": {"id": failed.get("node_id"), "type": failed.get("type"), "error": failed.get("error"),
             "input": failed.get("input"), "settings": settings} if failed else None, "steps_before": [{"type": s.get("type"), "status": s.get("status")} for s in steps[:30]]}
    secrets_ = load_secrets(ALL_PROJECTS)
    facts = redact(facts, secrets_)
    text = json.dumps(facts, default=str)
    if len(text) > 12_000: text = text[:12_000] + "...(shortened)"
    messages = [{"role": "system", "content": "You help people fix automation workflows. You are given a failed run as JSON. Explain in plain language, in at most 6 short sentences, why it most likely failed, then say exactly what to change (which setting or value). If you cannot tell, say what to check. Do not invent details that are not in the data."},
                {"role": "user", "content": text}]
    token = _secrets_var.set(secrets_)
    try:
        base, model, key = ai_connection({"credential": body.credential})
        explanation = _chat(base, model, key, messages, 0.2)
    except ValueError as exc: raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc: raise HTTPException(502, f"The AI service could not be used ({type(exc).__name__}). Check the credential and that the model is running.")
    finally: _secrets_var.reset(token)
    return {"explanation": redact({"t": explanation}, secrets_)["t"].strip(), "node_id": failed.get("node_id") if failed else None}


def _node_map(doc: dict) -> dict:
    return {n["id"]: n for n in doc.get("nodes", [])}


def diff_workflows(old: dict, new: dict) -> dict:
    """What changed between two saved definitions: nodes added, removed or edited (with the settings that differ) and connections added or removed."""
    a, b = _node_map(old), _node_map(new)
    changed = []
    for nid in sorted(set(a) & set(b)):
        da, db_ = a[nid].get("data") or {}, b[nid].get("data") or {}
        fields = sorted(k for k in set(da) | set(db_) if da.get(k) != db_.get(k))
        moved = a[nid].get("type") != b[nid].get("type")
        if fields or moved:
            changed.append({"id": nid, "type": b[nid].get("type"), "fields": [{"field": k, "before": str(da.get(k, ""))[:200], "after": str(db_.get(k, ""))[:200]} for k in fields[:30]],
                            **({"type_before": a[nid].get("type")} if moved else {})})
    edge = lambda d: {(e["source"], e["target"], e.get("sourceHandle") or "") for e in d.get("edges", [])}
    ea, eb = edge(old), edge(new)
    fmt = lambda s: [{"from": x[0], "to": x[1], **({"branch": x[2]} if x[2] else {})} for x in sorted(s)]
    return {"name": {"before": old.get("name"), "after": new.get("name")} if old.get("name") != new.get("name") else None,
            "nodes_added": [{"id": i, "type": b[i].get("type")} for i in sorted(set(b) - set(a))], "nodes_removed": [{"id": i, "type": a[i].get("type")} for i in sorted(set(a) - set(b))],
            "nodes_changed": changed, "edges_added": fmt(eb - ea), "edges_removed": fmt(ea - eb),
            "settings_changed": (old.get("settings") or {}) != (new.get("settings") or {})}


@app.get("/api/workflows/{workflow_id}/versions/{version_id}/diff")
def version_diff(workflow_id: str, version_id: int, against: str = "current"):
    """Compare a saved version with the current workflow (default) or with another saved version."""
    old = json.loads(load_version(workflow_id, version_id)["definition"])
    if against == "current": new = get_workflow(workflow_id)
    else:
        try: other = int(against)
        except ValueError: raise HTTPException(400, "against must be 'current' or a version id")
        new = json.loads(load_version(workflow_id, other)["definition"])
    return diff_workflows(old, new)


DECISION_RE = re.compile(r"[a-z0-9_-]{1,30}")


def deliver_response(exec_id: str, decision: str, body: Any) -> bool:
    """Answer the Wait for Response a run is paused at. False when nothing is waiting (already answered, timed out or cancelled)."""
    with conn() as db:
        w = db.execute("SELECT * FROM waits WHERE execution_id=? AND kind='callback' AND status='pending' ORDER BY created_at DESC LIMIT 1", (exec_id,)).fetchone()
        if not w or not db.execute("UPDATE waits SET status='running' WHERE id=? AND status='pending'", (w["id"],)).rowcount: return False  # claimed once, so a link works only once
        ex = db.execute("SELECT * FROM executions WHERE id=?", (exec_id,)).fetchone()
    if not ex:
        with conn() as db: db.execute("UPDATE waits SET status='done' WHERE id=?", (w["id"],))
        return False
    finish_wait(w, ex, {"decision": decision, "body": body})
    return True


def answer_page(title: str, text: str, status: int = 200) -> HTMLResponse:
    r = page(title, f"<h1>{html_lib.escape(title)}</h1><p>{html_lib.escape(text)}</p>")
    r.status_code = status
    return r


@app.api_route("/api/resume/{token}", methods=["GET", "POST"])
async def resume_by_link(token: str, request: Request, decision: str = "callback"):
    """Public link for a run that is waiting for a response. The signed token proves the link came from FlowPilot; each link works once."""
    exec_id = await run_in_threadpool(callback_exec_id, token)
    if not exec_id: raise HTTPException(404, "Not found")
    if not hook_allowed(token): raise HTTPException(429, "Too many requests")
    decision = decision.strip().lower()
    if not DECISION_RE.fullmatch(decision): raise HTTPException(400, "The decision uses letters, numbers, - and _ (up to 30 characters)")
    body: Any = None
    if request.method == "POST":
        raw = await request.body()
        if len(raw) > WEBHOOK_MAX_BODY: raise HTTPException(413, "Payload too large")
        if raw:
            try: body = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError: body = raw.decode("utf-8", errors="replace")[:5000]
    done = await run_in_threadpool(deliver_response, exec_id, decision, body)
    if request.method == "GET":
        return answer_page("Thank you" if done else "Nothing to answer", "Your answer was recorded." if done else "This request was already answered, has expired or was cancelled.", 200 if done else 409)
    if not done: raise HTTPException(409, "This request is no longer waiting for a response")
    return {"status": "resumed", "decision": decision}


class RespondIn(BaseModel):
    decision: str = Field(default="approve", max_length=30)
    body: Any = None


@app.post("/api/executions/{execution_id}/respond")
def respond_to_run(execution_id: str, body: RespondIn):
    """Answer a Wait for Response from the Executions page."""
    decision = body.decision.strip().lower()
    if not DECISION_RE.fullmatch(decision): raise HTTPException(400, "The decision uses letters, numbers, - and _ (up to 30 characters)")
    if not deliver_response(execution_id, decision, body.body): raise HTTPException(409, "This run is not waiting for a response")
    return {"id": execution_id, "decision": decision}


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
    if not handler_id and kind != "error": handler_id = default_error_workflow()  # the workflow's own choice wins; otherwise the shared one from Settings
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


# Live runs: a manual run started with ?live=true runs in a background thread, and its steps can be read while it works.
LIVE_RUNS: dict[str, dict] = {}
LIVE_LOCK = threading.Lock()
MAX_LIVE_RUNS = 40
LIVE_KEEP_SECONDS = 120


def live_register(workflow_id: str) -> dict:
    with LIVE_LOCK:
        cutoff = time.time() - LIVE_KEEP_SECONDS
        for key in [k for k, r in LIVE_RUNS.items() if r.get("finished") and r["finished"] < cutoff]: LIVE_RUNS.pop(key, None)
        if len(LIVE_RUNS) >= MAX_LIVE_RUNS: raise HTTPException(429, "Too many runs are in progress. Try again in a moment.")
        rec = {"id": str(uuid.uuid4()), "workflow_id": workflow_id, "steps": None, "secrets": {}, "result": None, "finished": None}
        LIVE_RUNS[rec["id"]] = rec
        return rec


def live_steps(rec: dict) -> list:
    """A copy of the steps so far with credential values hidden. The list is being changed by the run, so a rare clash is retried."""
    for _ in range(5):
        try: return redact(json.loads(json.dumps(list(rec["steps"] or []), default=str)), rec.get("secrets") or {})
        except RuntimeError: time.sleep(0.01)
    return []


def live_worker(rec: dict, doc: dict, initial: Any) -> None:
    _live_var.set(rec)
    try: result = execute_workflow(doc, "manual", initial, exec_id=rec["id"])
    except Exception as exc: result = {"id": rec["id"], "workflow_id": doc["id"], "trigger": "manual", "status": "failed", "output": None, "steps": [], "error": str(exc)}
    rec["result"], rec["finished"] = result, time.time()


@app.get("/api/live/{run_id}")
def live_run(run_id: str, request: Request):
    rec = LIVE_RUNS.get(run_id)
    visible = visible_workflow_ids(getattr(request.state, "actor", None))
    if not rec or (visible is not None and rec["workflow_id"] not in visible): raise HTTPException(404, "That run is no longer available")
    result = rec["result"]
    if result is not None: return {"id": run_id, "status": result["status"], "steps": result["steps"], "result": result}
    return {"id": run_id, "status": "running", "steps": live_steps(rec), "result": None}


@app.post("/api/workflows/{workflow_id}/run")
def run_workflow(workflow_id: str, body: RunIn | None = None, live: bool = False, dry: bool = False):
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
        if node and node["type"] == "poll_in": return SAMPLE_POLL_ITEM
        return None
    if dry:
        if live: raise HTTPException(400, "A dry run cannot also be a live run")
        noted: list = []
        token = _dry_var.set(noted)
        try: result = execute_workflow(doc, "dry", initial)
        finally: _dry_var.reset(token)
        return {**result, "dry_run": True, "not_done": noted}
    if live:
        rec = live_register(workflow_id)
        threading.Thread(target=live_worker, args=(rec, doc, initial), daemon=True, name="flowpilot-live-run").start()
        return {"id": rec["id"], "workflow_id": workflow_id, "status": "running", "live": True}
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


def get_tz(name: str):
    """An IANA time zone such as Europe/Paris; an empty name means this computer's own zone (None)."""
    name = str(name or "").strip()
    if not name: return None
    if not re.fullmatch(r"[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+){0,2}", name): raise ValueError(f"Unknown time zone: {name[:40]!r}")
    try: return ZoneInfo(name)
    except Exception: raise ValueError(f"Unknown time zone: {name!r}")


def cron_next(c: dict, after: datetime, tz=None) -> datetime:
    local = (after.astimezone(tz) + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for offset in range(0, 1830):  # look up to five years ahead
        day = (local + timedelta(days=offset)).date()
        if not cron_day_matches(c, day): continue
        for hour in c["hours"]:
            for minute in c["minutes"]:
                if offset == 0 and (hour, minute) < (local.hour, local.minute): continue
                return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(timezone.utc)
    raise ValueError("This cron expression never matches a real date")


def validate_schedule(data: dict) -> dict:
    cfg = _validate_schedule(data)
    tz_name = str(data.get("timezone") or "").strip()
    get_tz(tz_name)
    if tz_name and cfg["mode"] != "interval": cfg["tz"] = tz_name
    if cfg["mode"] == "cron": cron_next(cfg["cron"], datetime.now(timezone.utc), get_tz(tz_name))  # proves it can fire in that zone
    return cfg


def _validate_schedule(data: dict) -> dict:
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
        return {"mode": "cron", "cron": cron}
    raise ValueError(f"Unknown schedule mode: {mode}")


def next_run_after(cfg: dict, after: datetime) -> datetime:
    if cfg["mode"] == "interval": return after + timedelta(minutes=cfg["minutes"])
    tz = get_tz(cfg.get("tz", ""))
    if cfg["mode"] == "cron": return cron_next(cfg["cron"], after, tz)
    local = after.astimezone(tz)  # daily times are in the chosen time zone (default: this computer's)
    candidate = local.replace(hour=cfg["hour"], minute=cfg["minute"], second=0, microsecond=0, tzinfo=tz if tz else local.tzinfo)
    if candidate <= local: candidate = (local + timedelta(days=1)).replace(hour=cfg["hour"], minute=cfg["minute"], second=0, microsecond=0, tzinfo=tz if tz else local.tzinfo)
    return candidate.astimezone(timezone.utc)


def activation_next_run(doc: dict, after: datetime | None = None) -> str | None:
    """Validate that a workflow can be active and return its first run time (None for webhooks)."""
    starts = [n for n in doc["nodes"] if n.get("type") in START_TYPES]
    if len(starts) != 1: raise ValueError("A workflow needs exactly one trigger to be activated")
    node = starts[0]
    if node["type"] == "trigger": raise ValueError("Add a Schedule or Webhook trigger to activate this workflow")
    if node["type"] == "webhook":
        hook_auth_settings(node.get("data", {}) or {})
        parse_ip_rules((node.get("data") or {}).get("allowed_ips") or "")
        return None
    if node["type"] == "email_in":
        inbox_credential(node.get("data", {}))
        return (( after or datetime.now(timezone.utc)) + timedelta(minutes=inbox_minutes(node.get("data", {})))).isoformat()
    if node["type"] == "poll_in":
        validate_poll(node.get("data", {}) or {})
        return ((after or datetime.now(timezone.utc)) + timedelta(minutes=inbox_minutes(node.get("data", {})))).isoformat()
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
    if body.active: reset_poll_state(workflow_id)  # whatever exists now is the starting point
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
            if not node or node["type"] not in ("schedule", "email_in", "poll_in"):
                with conn() as db: db.execute("UPDATE workflows SET active=0, next_run_at=NULL WHERE id=?", (row["id"],))
                continue
            if node["type"] in ("email_in", "poll_in"):
                upcoming = (moment + timedelta(minutes=inbox_minutes(node.get("data", {})))).isoformat()
                with conn() as db: db.execute("UPDATE workflows SET next_run_at=? WHERE id=?", (upcoming, row["id"]))
                submit_inbox_poll(doc, node)
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


def hook_auth_settings(data: dict) -> tuple[str, str, str]:
    kind = str(data.get("auth_type") or "none")
    if kind not in ("none", "header", "basic", "hmac"): raise ValueError("Webhook authentication must be none, header, basic or hmac")
    if kind == "none": return kind, "", ""
    header = str(data.get("auth_header") or ("X-Signature-256" if kind == "hmac" else "X-Webhook-Token")).strip()
    if kind == "hmac":
        stamp = str(data.get("auth_timestamp_header") or "").strip()
        if stamp and (not HEADER_NAME_RE.fullmatch(stamp) or stamp.lower() in BLOCKED_HEADERS): raise ValueError(f"Invalid header name: {stamp!r}")
    if kind in ("header", "hmac") and (not HEADER_NAME_RE.fullmatch(header) or header.lower() in BLOCKED_HEADERS): raise ValueError(f"Invalid header name: {header!r}")
    cred = str(data.get("auth_credential") or "").strip()
    if not cred: raise ValueError("Choose a credential that holds the secret for webhook authentication")
    return kind, header, cred


def hook_denied(node: dict | None, headers: Any, project: str | None = ALL_PROJECTS) -> bool:
    """True when the webhook asks for authentication and the request does not supply it. Compares in constant time."""
    data = (node or {}).get("data") or {}
    try: kind, header, cred = hook_auth_settings(data)
    except ValueError: return True  # a broken setting must never open the door
    if kind in ("none", "hmac"): return False  # a signed webhook is checked once the body has been read (hmac_denied)
    secret = load_secrets(project).get(cred)
    if not secret: return True
    if kind == "header": supplied = headers.get(header, "")
    else:
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("basic "): return True
        try: supplied = base64.b64decode(auth[6:].strip(), validate=True).decode("utf-8")
        except Exception: return True
    return not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8"))


def parse_ip_rules(text: str) -> list:
    """Addresses or ranges (10.0.0.0/8, 203.0.113.7, 2001:db8::/32) separated by commas or spaces. Raises ValueError for anything else."""
    import ipaddress
    rules = [x for x in re.split(r"[,\s]+", str(text or "").strip()) if x]
    if len(rules) > 50: raise ValueError("At most 50 allowed addresses")
    try: return [ipaddress.ip_network(r, strict=False) for r in rules]
    except ValueError: raise ValueError("Allowed addresses are IPs or ranges such as 203.0.113.7 or 10.0.0.0/8, separated by commas")


def ip_allowed(node: dict | None, client_ip: str) -> bool:
    """True when the webhook has no allow-list or the caller's address is on it. A broken list refuses everyone rather than opening the door."""
    import ipaddress
    text = str(((node or {}).get("data") or {}).get("allowed_ips") or "").strip()
    if not text: return True
    try:
        rules = parse_ip_rules(text)
        ip = ipaddress.ip_address(client_ip)
        if getattr(ip, "ipv4_mapped", None): ip = ip.ipv4_mapped
    except ValueError: return False
    return any(ip.version == r.version and ip in r for r in rules)


HMAC_TOLERANCE = 300  # seconds a signed request stays valid


def hmac_denied(node: dict | None, headers: Any, raw: bytes, project: str | None = ALL_PROJECTS) -> bool:
    """True when a signed webhook has no valid signature. The signature is HMAC-SHA256 (hex, optionally sha256= prefixed) of the raw body,
    or of '<timestamp>.<body>' when a timestamp header is configured; then requests older (or newer) than five minutes are refused."""
    data = (node or {}).get("data") or {}
    try: kind, header, cred = hook_auth_settings(data)
    except ValueError: return True
    if kind != "hmac": return False
    secret = load_secrets(project).get(cred)
    if not secret: return True
    message = raw
    stamp_header = str(data.get("auth_timestamp_header") or "").strip()
    if stamp_header:
        stamp = str(headers.get(stamp_header, "")).strip()
        try: sent_at = float(stamp)
        except ValueError: return True
        if abs(time.time() - sent_at) > HMAC_TOLERANCE: return True
        message = stamp.encode() + b"." + raw
    supplied = str(headers.get(header, "")).strip().lower()
    if supplied.startswith("sha256="): supplied = supplied[7:]
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


IDEMPOTENCY_HOURS = 24


def is_duplicate_request(doc: dict, headers: Any) -> bool:
    """With 'Ignore repeated requests' on, a request whose Idempotency-Key was already seen in the last day is answered but not run again."""
    node = start_node(doc) or {}
    if str((node.get("data") or {}).get("idempotency") or "off") != "on": return False
    key = str(headers.get("idempotency-key", "")).strip()[:200]
    if not key: return False
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=IDEMPOTENCY_HOURS)).isoformat()
    with conn() as db:
        db.execute("DELETE FROM hook_keys WHERE seen_at < ?", (cutoff,))
        try: db.execute("INSERT INTO hook_keys VALUES (?,?,?)", (doc["id"], key, now()))
        except Exception: return True
    return False


def release_request_key(doc: dict, headers: Any) -> None:
    """A run that failed frees its Idempotency-Key, so the sender's retry is not mistaken for a repeat of a request that worked."""
    key = str(headers.get("idempotency-key", "")).strip()[:200]
    if key:
        with conn() as db: db.execute("DELETE FROM hook_keys WHERE workflow_id=? AND key=?", (doc["id"], key))


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
        out.append({"name": name, "label": str(it.get("label") or name)[:100], "type": kind if kind in ("text", "email", "number", "textarea", "file") else "text"})
    return out[:20]


UPLOAD_MAX_TOTAL = 10_000_000
UPLOAD_MAX_FILES = 5


def accepts_files(node: dict | None) -> bool:
    return str(((node or {}).get("data") or {}).get("accept_files") or "false") == "true" or any(f["type"] == "file" for f in form_fields(node))


def parse_multipart(raw: bytes, ctype: str) -> tuple[dict, list[tuple[str, str, str, bytes]]]:
    """Split a multipart/form-data body into text fields and files (field, filename, content type, bytes)."""
    msg = BytesParser(policy=email_policy.HTTP).parsebytes(b"Content-Type: " + ctype.encode("latin-1", "ignore") + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw)
    if not msg.is_multipart(): raise ValueError("This is not a valid multipart body")
    fields: dict[str, str] = {}
    files: list[tuple[str, str, str, bytes]] = []
    for part in msg.iter_parts():
        if len(fields) + len(files) >= 40: raise ValueError("Too many form parts")
        if part.get_content_disposition() != "form-data": continue
        name = str(part.get_param("name", header="content-disposition") or "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", name): continue
        data = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is None: fields[name] = data.decode("utf-8", errors="replace")[:5000]
        elif filename or data: files.append((name, str(filename), part.get_content_type(), data))  # an empty file input (no file chosen) is ignored
    return fields, files


def safe_upload_name(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return (re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "file")[-100:]


def save_uploads(files: list[tuple[str, str, str, bytes]]) -> list[dict]:
    """Store uploaded files under files/uploads/<date>/ and describe them. The Files node can read them back by path."""
    folder = files_dir() / "uploads" / datetime.now(timezone.utc).strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for field, filename, content_type, data in files:
        stored = f"{uuid.uuid4().hex[:8]}-{safe_upload_name(filename)}"
        (folder / stored).write_bytes(data)
        saved.append({"field": field, "filename": filename[:200], "content_type": content_type[:100], "size": len(data), "path": f"uploads/{folder.name}/{stored}"})
    return saved


def prune_uploads(days: int) -> int:
    """Delete upload folders older than the retention period. Returns how many folders were removed."""
    root, removed = files_dir() / "uploads", 0
    if not root.is_dir(): return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
    for d in root.iterdir():
        if d.is_dir() and re.fullmatch(r"\d{8}", d.name) and d.name < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


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
    enctype = " enctype='multipart/form-data'" if any(f["type"] == "file" for f in fields) else ""
    return page(doc.get("name", "Form"), f"<h1>{html_lib.escape(doc.get('name', 'Form'))}</h1><p>{desc}</p><form method=post action=''{enctype}>{''.join(rows)}<button type=submit>Submit</button></form>")


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
    if not ip_allowed(start_node(doc), request.client.host if request.client else ""): raise HTTPException(403, "This address may not call this webhook")
    if await run_in_threadpool(hook_denied, start_node(doc), request.headers, await run_in_threadpool(workflow_project, doc)):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": 'Basic realm="webhook"'} if str(((start_node(doc) or {}).get("data") or {}).get("auth_type")) == "basic" else None)
    signed = str(((start_node(doc) or {}).get("data") or {}).get("auth_type")) == "hmac"
    fields = [] if signed else form_fields(start_node(doc))
    if request.method == "GET" and fields: return render_form(doc, fields)
    ctype = request.headers.get("content-type", "")
    kind = ctype.split(";")[0].strip().lower()
    upload = kind == "multipart/form-data"
    if upload and not accepts_files(start_node(doc)): raise HTTPException(415, "File uploads are not enabled for this webhook")
    limit = UPLOAD_MAX_TOTAL if upload else WEBHOOK_MAX_BODY
    try: declared = int(request.headers.get("content-length") or 0)
    except ValueError: declared = 0
    if declared > limit: raise HTTPException(413, "Payload too large")
    chunks, size = [], 0
    async for chunk in request.stream():  # read in pieces so an oversized body is refused without holding it all in memory
        size += len(chunk)
        if size > limit: raise HTTPException(413, "Payload too large")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if signed and await run_in_threadpool(hmac_denied, start_node(doc), request.headers, raw, await run_in_threadpool(workflow_project, doc)): raise HTTPException(401, "Unauthorized")
    if await run_in_threadpool(is_duplicate_request, doc, request.headers):
        return JSONResponse({"status": "duplicate"}, status_code=200)
    body: Any = None
    if upload and raw:
        try: sent, found = await run_in_threadpool(parse_multipart, raw, ctype)
        except ValueError as exc: raise HTTPException(400, str(exc))
        if len(found) > UPLOAD_MAX_FILES: raise HTTPException(413, f"At most {UPLOAD_MAX_FILES} files per request")
        if any(len(f[3]) > FILE_MAX for f in found): raise HTTPException(413, "A file is larger than 5 MB")
        body = {f["name"]: sent.get(f["name"], "") for f in fields if f["type"] != "file"} if fields else sent
        body["files"] = await run_in_threadpool(save_uploads, found)
    elif raw:
        text = raw.decode("utf-8", errors="replace")
        if ctype.split(";")[0].strip() == "application/x-www-form-urlencoded":
            sent = {k: v[0][:5000] for k, v in parse_qs(text, keep_blank_values=True).items()}
            body = {f["name"]: sent.get(f["name"], "") for f in fields} if fields else sent
        else:
            try: body = json.loads(text)
            except ValueError: body = text
    is_form = kind in ("application/x-www-form-urlencoded", "multipart/form-data") and "text/html" in request.headers.get("accept", "")
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
    if result["status"] == "failed": await run_in_threadpool(release_request_key, doc, request.headers)
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
            try: prune_uploads(cfg["retention_days"])
            except OSError: log.exception("Pruning uploaded files failed")
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


def default_error_workflow() -> str:
    with conn() as db: row = db.execute("SELECT value FROM settings WHERE key='default_error_workflow'").fetchone()
    return row["value"] if row else ""


class ErrorWorkflowIn(BaseModel):
    workflow_id: str = Field(default="", max_length=100)


@app.get("/api/settings/error-workflow")
def read_error_workflow():
    return {"workflow_id": default_error_workflow()}


@app.put("/api/settings/error-workflow")
def write_error_workflow(body: ErrorWorkflowIn):
    wid = body.workflow_id.strip()
    if wid:
        with conn() as db:
            if not db.execute("SELECT 1 FROM workflows WHERE id=?", (wid,)).fetchone(): raise HTTPException(404, "Workflow not found")
    with conn() as db:
        if wid: db.execute("INSERT INTO settings (key,value) VALUES ('default_error_workflow',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (wid,))
        else: db.execute("DELETE FROM settings WHERE key='default_error_workflow'")
    return {"workflow_id": wid}


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
        item = {k: d.get(k) for k in ("name", "description", "tags", "nodes", "edges", "settings")}
        with conn() as db: item["tests"] = [{k: t[k] for k in ("name", "input", "expected", "mode", "expect")} for t in (_test_row(x) for x in db.execute("SELECT * FROM workflow_tests WHERE workflow_id=? ORDER BY created_at, id", (d["id"],)).fetchall())]
        items.append(item)
    with conn() as db: conns = {r["name"]: json.loads(r["manifest"]) for r in db.execute("SELECT name, manifest FROM connectors ORDER BY name").fetchall()}
    body = {"format": "flowpilot-backup", "version": 1, "exported_at": now(), "workflows": items, "connectors": conns}  # connectors hold addresses and field templates, never secrets
    return JSONResponse(body, headers={"Content-Disposition": 'attachment; filename="flowpilot-workflows.json"'})


class RestoreIn(BaseModel):
    format: str
    workflows: list[dict[str, Any]] = Field(max_length=200)
    connectors: dict[str, Any] = Field(default_factory=dict, max_length=60)


CRED_REF_RE = re.compile(r"cred\.([A-Za-z][A-Za-z0-9_-]{0,39})")


def credentials_used(doc: dict) -> list[str]:
    """Names of credentials a workflow refers to, through {{cred.NAME}} or a credential field. Shown after an import so it can be reviewed first."""
    found: set[str] = set()
    for n in doc.get("nodes") or []:
        data = n.get("data") or {}
        for key, value in data.items():
            if isinstance(value, str):
                found.update(CRED_REF_RE.findall(value))
                if key in ("credential", "auth_credential") and NAME_RE.fullmatch(value.strip()): found.add(value.strip())
    return sorted(found)[:20]


@app.post("/api/backup/restore")
def restore_workflows(body: RestoreIn):
    """Add the workflows from a backup file as new, inactive workflows. Nothing existing is overwritten."""
    return apply_backup(body)


class ImportUrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)


@app.post("/api/backup/import-url")
def import_from_link(body: ImportUrlIn):
    """Fetch a template pack (the same file as a workflow backup) from a public HTTPS link and add it. Nothing runs and nothing is overwritten."""
    try:
        safe_url(body.url.strip())
        data = do_request("GET", body.url.strip(), {"Accept": "application/json"}, timeout=10.0)
    except ValueError as exc: raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc: raise HTTPException(400, f"Could not download that file ({type(exc).__name__})")
    if not isinstance(data, dict): raise HTTPException(400, "That link did not return a FlowPilot file")
    try: pack = RestoreIn(**data)
    except Exception: raise HTTPException(400, "That file is not a FlowPilot backup or template pack")
    return apply_backup(pack)


def restore_tests(workflow_id: str, tests: Any) -> int:
    """Saved test cases that came with a workflow in a backup. Invalid ones are dropped; the workflow itself is unaffected."""
    kept = 0
    for t in (tests if isinstance(tests, list) else [])[:MAX_TESTS]:
        try: case = TestCaseIn(**t)
        except Exception: continue
        spec = {"input": case.input, "expected": case.expected, "mode": case.mode, "expect": case.expect}
        if len(json.dumps(spec, default=str)) > 100_000: continue
        with conn() as db:
            if db.execute("SELECT 1 FROM workflow_tests WHERE workflow_id=? AND name=?", (workflow_id, case.name)).fetchone(): continue
            db.execute("INSERT INTO workflow_tests (id,workflow_id,name,spec,created_at) VALUES (?,?,?,?,?)", (str(uuid.uuid4()), workflow_id, case.name, json.dumps(spec, default=str), now()))
        kept += 1
    return kept


def apply_backup(body: RestoreIn) -> dict:
    if body.format != "flowpilot-backup": raise HTTPException(400, "This is not a FlowPilot backup file")
    created, skipped = [], []
    for item in body.workflows:
        label = str(item.get("name") or "(unnamed)")[:120]
        try:
            wf = WorkflowIn(name=item.get("name") or "Restored workflow", description=item.get("description") or "", nodes=item.get("nodes") or [], edges=item.get("edges") or [], tags=item.get("tags") or [], settings=item.get("settings") or {})
            validate_flow(wf)
        except HTTPException as exc: skipped.append({"name": label, "reason": exc.detail}); continue
        except Exception as exc: skipped.append({"name": label, "reason": str(exc).splitlines()[0][:150]}); continue
        wid = str(uuid.uuid4())
        doc = wf.model_dump()
        doc["id"] = wid
        with conn() as db: insert_workflow(db, wid, wf.name, wf.description, doc, now())
        kept = restore_tests(wid, item.get("tests"))
        created.append({"id": wid, "name": wf.name, "uses_credentials": credentials_used(doc), "tests": kept})
    made, passed = [], []
    for cname, manifest in body.connectors.items():
        try:
            if not connector_lib.NAME_RE.fullmatch(str(cname)): raise ValueError("invalid name")
            clean = connector_lib.validate_manifest(manifest)
            with conn() as db:
                if db.execute("SELECT 1 FROM connectors WHERE name=?", (cname,)).fetchone(): raise ValueError("already exists")
                if db.execute("SELECT COUNT(*) c FROM connectors").fetchone()["c"] >= MAX_CONNECTORS: raise ValueError("too many connectors")
                db.execute("INSERT INTO connectors (name,manifest,updated_at) VALUES (?,?,?)", (cname, json.dumps(clean), now()))
            made.append(cname)
        except ValueError as exc: passed.append({"name": str(cname)[:60], "reason": str(exc)[:150]})
    return {"created": created, "skipped": skipped, "connectors_created": made, "connectors_skipped": passed}


@app.get("/api/backup/database")
def backup_database():
    """A consistent copy of the whole database (workflows, runs, users, encrypted credentials). The key file is not included."""
    if DATABASE_URL: raise HTTPException(400, "FlowPilot is using Postgres. Back it up with pg_dump (workflow export and import still work here).")
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


def csv_safe(value: Any) -> str:
    """Stop spreadsheets from treating a cell as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@app.get("/api/executions/export")
def export_executions(request: Request, workflow_id: str | None = None, status: str | None = None):
    visible = visible_workflow_ids(getattr(request.state, "actor", None))
    with conn() as db:
        names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM workflows").fetchall()}
        rows = db.execute("SELECT * FROM executions ORDER BY started_at DESC LIMIT 20000").fetchall()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["execution_id", "workflow", "status", "trigger", "started_at", "finished_at", "duration_seconds", "steps", "error"])
    count = 0
    for r in rows:
        if visible is not None and r["workflow_id"] not in visible: continue
        if workflow_id and r["workflow_id"] != workflow_id: continue
        if status and r["status"] != status: continue
        try: seconds = round((datetime.fromisoformat(r["finished_at"]) - datetime.fromisoformat(r["started_at"])).total_seconds(), 3) if r["finished_at"] else ""
        except ValueError: seconds = ""
        writer.writerow([csv_safe(x) for x in (r["id"], names.get(r["workflow_id"], "(deleted workflow)"), r["status"], r["trigger_kind"], r["started_at"], r["finished_at"], seconds, len(json.loads(r["steps"] or "[]")), r["error"])])
        count += 1
        if count >= 5000: break
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="flowpilot-runs.csv"'})


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
        waits = {w["execution_id"]: (w["resume_at"], w["kind"]) for w in db.execute("SELECT execution_id, resume_at, kind FROM waits WHERE status='pending'").fetchall()}
    return [{"id":r["id"],"workflow_id":r["workflow_id"],"status":r["status"],"started_at":r["started_at"],"error":r["error"],"trigger":r["trigger_kind"],"steps":json.loads(r["steps"] or "[]"),"output":json.loads(r["output"] or "null"),
             "resume_at":waits.get(r["id"], (None, None))[0], "waiting_for": "response" if waits.get(r["id"], (None, None))[1] == "callback" else None,
             "ai_usage": json.loads(r["ai_usage"]) if r["ai_usage"] else None} for r in rows]


STATS_WINDOWS = {"24h": 1, "7d": 7, "30d": 30}


def duration_seconds(started: str | None, finished: str | None) -> float | None:
    try: return max(0.0, (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds())
    except (TypeError, ValueError): return None


@app.get("/api/stats")
def run_stats(request: Request, window: str = "7d"):
    """Success and failure counts, average run time and the workflows that fail most, for the runs this person may see."""
    if window not in STATS_WINDOWS: raise HTTPException(400, "window must be 24h, 7d or 30d")
    visible = visible_workflow_ids(getattr(request.state, "actor", None))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STATS_WINDOWS[window])).isoformat()
    with conn() as db:
        rows = db.execute("SELECT workflow_id, status, started_at, finished_at, ai_usage FROM executions WHERE started_at >= ?", (cutoff,)).fetchall()
        names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM workflows").fetchall()}
    rows = [r for r in rows if visible is None or r["workflow_id"] in visible]
    done = [r for r in rows if r["status"] in ("success", "failed")]
    failed = [r for r in done if r["status"] == "failed"]
    times = [d for d in (duration_seconds(r["started_at"], r["finished_at"]) for r in done) if d is not None]
    per: dict[str, int] = {}
    for r in failed: per[r["workflow_id"]] = per.get(r["workflow_id"], 0) + 1
    top = sorted(per.items(), key=lambda kv: (-kv[1], names.get(kv[0], "")))[:5]
    usage = [json.loads(r["ai_usage"]) for r in rows if r["ai_usage"]]
    return {"window": window, "total": len(rows), "success": len(done) - len(failed), "failed": len(failed), "waiting": sum(1 for r in rows if r["status"] == "waiting"),
            "failure_rate": round(len(failed) / len(done), 4) if done else None, "average_seconds": round(sum(times) / len(times), 3) if times else None,
            "ai_calls": sum(u.get("calls", 0) for u in usage), "ai_tokens": sum(u.get("tokens", 0) for u in usage),
            "top_failing": [{"workflow_id": w, "name": names.get(w, "(deleted)"), "failed": n} for w, n in top]}


@app.get("/api/metrics")
def metrics():
    """Prometheus text format for owners: run counts by status, active workflows and queue depth."""
    with conn() as db:
        by = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) n FROM executions GROUP BY status").fetchall()}
        active = db.execute("SELECT COUNT(*) n FROM workflows WHERE active=1").fetchone()["n"]
        total = db.execute("SELECT COUNT(*) n FROM workflows").fetchone()["n"]
        queued = db.execute("SELECT COUNT(*) n FROM jobs WHERE status='queued'").fetchone()["n"]
    lines = ["# HELP flowpilot_runs Stored runs by status.", "# TYPE flowpilot_runs gauge"]
    lines += [f'flowpilot_runs{{status="{re.sub(r"[^a-z_]", "", s)}"}} {n}' for s, n in sorted(by.items())]
    lines += ["# HELP flowpilot_workflows Workflows by state.", "# TYPE flowpilot_workflows gauge", f'flowpilot_workflows{{state="active"}} {active}', f'flowpilot_workflows{{state="total"}} {total}',
              "# HELP flowpilot_queue_jobs Jobs waiting for a worker.", "# TYPE flowpilot_queue_jobs gauge", f"flowpilot_queue_jobs {queued}",
              "# HELP flowpilot_info Version of this FlowPilot.", "# TYPE flowpilot_info gauge", f'flowpilot_info{{version="{VERSION}"}} 1']
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


# ---------------------------------------------------------------------------
# Single sign-on (OpenID Connect, authorization code flow with PKCE). Local accounts keep working.
# Signing in creates or reuses an account of its own kind ("sso"); it never takes over a local account.
# ---------------------------------------------------------------------------
SSO_SECRET_NAME = "SSO_CLIENT_SECRET"
SSO_ROLES = ("viewer", "editor", "admin")
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
_sso_cache: dict[str, tuple[float, Any]] = {}
SSO_CACHE_SECONDS = 600


def sso_config() -> dict:
    with conn() as db: rows = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings WHERE key LIKE 'sso_%'").fetchall()}
    with conn() as db: has_secret = db.execute("SELECT 1 FROM credentials WHERE name=?", (SSO_SECRET_NAME,)).fetchone() is not None
    cfg = {"enabled": rows.get("sso_enabled") == "1", "issuer": rows.get("sso_issuer", ""), "client_id": rows.get("sso_client_id", ""), "allowed_domains": rows.get("sso_allowed_domains", ""),
           "default_role": rows.get("sso_default_role", "viewer") if rows.get("sso_default_role") in SSO_ROLES else "viewer", "has_secret": has_secret}
    cfg["ready"] = bool(cfg["enabled"] and cfg["issuer"] and cfg["client_id"] and has_secret and cfg["allowed_domains"])
    return cfg


def sso_redirect_uri() -> str: return public_url() + "/api/auth/sso/callback"


def sso_url(url: str) -> str:
    """The provider's addresses must be public HTTPS, unless private hosts were allowed for a provider on your own network."""
    if os.getenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS") == "1":
        if urlparse(url).scheme not in ("http", "https"): raise ValueError("Provider addresses must be http or https")
        return url
    return safe_url(url)


def sso_cached(key: str, load, fresh: bool = False) -> Any:
    hit = _sso_cache.get(key)
    if hit and not fresh and time.time() - hit[0] < SSO_CACHE_SECONDS: return hit[1]
    value = load()
    _sso_cache[key] = (time.time(), value)
    return value


def sso_discovery(issuer: str, fresh: bool = False) -> dict:
    def load() -> dict:
        res = do_request("GET", sso_url(issuer.rstrip("/") + "/.well-known/openid-configuration"), {"Accept": "application/json"})
        if not isinstance(res, dict): raise ValueError("The provider did not return a discovery document")
        if str(res.get("issuer", "")).rstrip("/") != issuer.rstrip("/"): raise ValueError("The provider's issuer does not match the one you entered")
        for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(res.get(k), str) or not res[k].startswith(("https://", "http://")): raise ValueError(f"The provider's discovery document has no {k}")
        return res
    return sso_cached("disc:" + issuer, load, fresh)


def sso_keys(disc: dict, fresh: bool = False) -> list[dict]:
    def load() -> list:
        res = do_request("GET", sso_url(disc["jwks_uri"]), {"Accept": "application/json"})
        keys = res.get("keys") if isinstance(res, dict) else None
        if not isinstance(keys, list): raise ValueError("The provider did not return its signing keys")
        return keys
    return sso_cached("jwks:" + disc["jwks_uri"], load, fresh)


class SsoIn(BaseModel):
    enabled: bool = False
    issuer: str = Field(default="", max_length=300)
    client_id: str = Field(default="", max_length=300)
    client_secret: str | None = Field(default=None, max_length=500)
    allowed_domains: str = Field(default="", max_length=500)
    default_role: str = "viewer"


def public_sso(cfg: dict) -> dict:
    return {k: cfg[k] for k in ("enabled", "issuer", "client_id", "allowed_domains", "default_role", "has_secret", "ready")} | {"redirect_uri": sso_redirect_uri()}


@app.get("/api/sso/config")
def get_sso_config(): return public_sso(sso_config())


@app.put("/api/sso/config")
def put_sso_config(body: SsoIn):
    issuer = body.issuer.strip().rstrip("/")
    domains = [d.strip().lower().lstrip("@") for d in body.allowed_domains.replace(";", ",").split(",") if d.strip()]
    if issuer and (not issuer.startswith(("https://", "http://")) or any(c.isspace() for c in issuer)): raise HTTPException(400, "The issuer must be an address starting with https://")
    if issuer.startswith("http://") and os.getenv("FLOWPILOT_ALLOW_PRIVATE_HOSTS") != "1": raise HTTPException(400, "The issuer must use https (plain http needs FLOWPILOT_ALLOW_PRIVATE_HOSTS=1)")
    if body.client_id and (any(c.isspace() for c in body.client_id)): raise HTTPException(400, "The client ID cannot contain spaces")
    if any(not DOMAIN_RE.fullmatch(d) for d in domains) or len(domains) > 20: raise HTTPException(400, "Allowed domains look like example.com (up to 20, separated by commas)")
    if body.default_role not in SSO_ROLES: raise HTTPException(400, "The default role must be viewer, editor or admin")
    if body.client_secret:
        if any(c.isspace() for c in body.client_secret): raise HTTPException(400, "The client secret cannot contain spaces")
        store_credential(SSO_SECRET_NAME, body.client_secret)
    with conn() as db:
        for k, v in (("sso_issuer", issuer), ("sso_client_id", body.client_id.strip()), ("sso_allowed_domains", ",".join(domains)), ("sso_default_role", body.default_role)):
            db.execute("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    cfg = sso_config()
    if body.enabled and not (cfg["issuer"] and cfg["client_id"] and cfg["has_secret"] and cfg["allowed_domains"]):
        raise HTTPException(400, "To turn single sign-on on, fill in the issuer, client ID, client secret and at least one allowed domain")
    with conn() as db: db.execute("INSERT INTO settings (key,value) VALUES ('sso_enabled', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if body.enabled else "0",))
    _sso_cache.clear()
    return public_sso(sso_config())


@app.post("/api/sso/test")
def test_sso():
    cfg = sso_config()
    if not cfg["issuer"]: raise HTTPException(400, "Enter the issuer first and save")
    try:
        disc = sso_discovery(cfg["issuer"], fresh=True)
        keys = sso_keys(disc, fresh=True)
    except ValueError as exc: raise HTTPException(400, str(exc))
    except httpx.HTTPError as exc: raise HTTPException(400, f"Could not reach the provider: {type(exc).__name__}")
    return {"ok": True, "authorization_endpoint": disc["authorization_endpoint"], "token_endpoint": disc["token_endpoint"], "keys": len(keys), "redirect_uri": sso_redirect_uri()}


def sso_back(kind: str, reason: str = "") -> RedirectResponse:
    return RedirectResponse(f"/?sso={kind}" + (f"&reason={quote(reason[:160])}" if reason else ""), status_code=303)


@app.get("/api/auth/sso/start")
def sso_start():
    cfg = sso_config()
    if not cfg["ready"]: raise HTTPException(404, "Single sign-on is not turned on")
    try: disc = sso_discovery(cfg["issuer"])
    except (ValueError, httpx.HTTPError) as exc: return sso_back("error", f"Could not reach the sign-in provider ({str(exc)[:100]})")
    verifier, state, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    with conn() as db:
        db.execute("DELETE FROM oauth_pending WHERE created_at < ?", (time.time() - OAUTH_STATE_SECONDS,))
        db.execute("INSERT INTO oauth_pending (state,payload_enc,created_at) VALUES (?,?,?)", (state, fernet().encrypt(json.dumps({"kind": "sso", "verifier": verifier, "nonce": nonce}).encode()).decode(), time.time()))
    params = {"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": sso_redirect_uri(), "scope": "openid email profile", "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
    url = disc["authorization_endpoint"]
    return RedirectResponse(url + ("&" if "?" in url else "?") + urlencode(params), status_code=303)


def sso_user(email: str, role: str) -> dict:
    """The account for a verified address: reuse a single sign-on account, create one, but never touch a local account."""
    if not re.fullmatch(r"[a-z0-9_.+\-]+@[a-z0-9.\-]+", email) or len(email) > 40: raise ValueError("This address cannot be used as a username here (it must be at most 40 letters, numbers and . _ + - @)")
    with conn() as db:
        row = db.execute("SELECT id, role, auth_source FROM users WHERE username=?", (email,)).fetchone()
        if row:
            if row["auth_source"] != "sso": raise ValueError("A local account already uses this address. Sign in with its password.")
            return {"id": row["id"], "role": row["role"]}
        if db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] >= MAX_USERS: raise ValueError(f"Maximum {MAX_USERS} users")
        uid = str(uuid.uuid4())
        db.execute("INSERT INTO users (id,username,password_hash,created_at,role,auth_source) VALUES (?,?,?,?,?,'sso')", (uid, email, "sso$no-password", now(), role))
    return {"id": uid, "role": role}


@app.get("/api/auth/sso/callback")
def sso_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    with conn() as db:
        row = db.execute("SELECT payload_enc, created_at FROM oauth_pending WHERE state=?", (state,)).fetchone()
        if row: db.execute("DELETE FROM oauth_pending WHERE state=?", (state,))  # single use
    cfg = sso_config()
    if not cfg["ready"]: return sso_back("error", "Single sign-on is not turned on")
    if not row or time.time() - row["created_at"] > OAUTH_STATE_SECONDS: return sso_back("error", "This sign-in expired or was not started here. Try again.")
    try: pending = json.loads(fernet().decrypt(row["payload_enc"].encode()).decode())
    except Exception: return sso_back("error", "This sign-in expired or was not started here. Try again.")
    if pending.get("kind") != "sso": return sso_back("error", "This sign-in expired or was not started here. Try again.")
    if error or not code: return sso_back("error", f"The provider refused the sign-in: {error or 'no code returned'}"[:160])
    try:
        disc = sso_discovery(cfg["issuer"])
        secret = load_secrets().get(SSO_SECRET_NAME, "")
        res = do_request("POST", sso_url(disc["token_endpoint"]), {"Accept": "application/json"}, data={"grant_type": "authorization_code", "code": code, "redirect_uri": sso_redirect_uri(),
                         "client_id": cfg["client_id"], "client_secret": secret, "code_verifier": pending["verifier"]})
        if not isinstance(res, dict) or not res.get("id_token"): raise ValueError("The provider did not return an ID token")
        try: claims = verify_id_token(res["id_token"], sso_keys(disc), issuer=disc["issuer"], client_id=cfg["client_id"], nonce=pending["nonce"])
        except ValueError as first:
            if "signing key not found" not in str(first): raise
            claims = verify_id_token(res["id_token"], sso_keys(disc, fresh=True), issuer=disc["issuer"], client_id=cfg["client_id"], nonce=pending["nonce"])  # the provider may have rotated its keys
        email = str(claims.get("email") or "").strip().lower()
        if not email or claims.get("email_verified") not in (True, "true"): raise ValueError("The provider did not confirm your email address")
        if email.rsplit("@", 1)[-1] not in cfg["allowed_domains"].split(","): raise ValueError("Your email domain is not allowed to sign in here")
        account = sso_user(email, cfg["default_role"])
    except (ValueError, httpx.HTTPError) as exc:
        msg = str(exc)
        for hidden in (load_secrets().get(SSO_SECRET_NAME, ""), code):
            if hidden: msg = msg.replace(hidden, "[hidden]")
        return sso_back("error", msg)
    response = RedirectResponse("/", status_code=303)
    create_session(account["id"], response, is_https(request))
    try: record_audit(email, account["role"], "SSO", "/api/auth/sso/callback", 200)
    except Exception: log.exception("Could not write the audit log")
    return response


def mount_ui(target, dist: Path) -> bool:
    """Serve a built frontend (index.html and assets) from the API itself. Unknown paths fall back to index.html for the single-page app."""
    index = dist / "index.html"
    if not index.is_file(): return False
    root = dist.resolve()
    if (root / "assets").is_dir(): target.mount("/assets", StaticFiles(directory=root / "assets"), name="ui-assets")

    @target.get("/{path:path}", include_in_schema=False)
    def ui(path: str):
        if path == "api" or path.startswith("api/"): raise HTTPException(404, "Not found")
        file = (root / path).resolve()
        if path and file.is_file() and root in file.parents: return FileResponse(file)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return True


if os.getenv("FLOWPILOT_SERVE_UI", "0") == "1":
    if not mount_ui(app, Path(os.getenv("FLOWPILOT_UI_DIR", str(Path(__file__).resolve().parents[2] / "frontend" / "dist")))):
        log.warning("FLOWPILOT_SERVE_UI is on but no built interface was found. Run npm run build in the frontend folder.")
