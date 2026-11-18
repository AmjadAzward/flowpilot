# FlowPilot Product Roadmap

This file describes planned functionality, **not current features**.

## Shipped
### v0.1 starter
- Full-stack local web app (React / TypeScript / FastAPI / SQLite)
- Visual node editor, manual execution, workflow CRUD, import/export, logs
- Local Windows startup script

### v0.2
- True boolean branch routing from Compare Value (true / false outputs)
- Text Template and Delay nodes, seven comparison operators
- Fan-out from a node to several children
- Per-step timings, per-node run badges on the canvas
- Workflow duplication, execution filtering and clearing, starter templates
- Light and dark themes, unsaved-change indicator, Ctrl+S
- Redesigned light/dark interface with an orange brand theme, branded tab title and icon
- Owner login with hashed passwords, sessions, lockout and password reset script
- 65 API tests and a 23-check browser end-to-end suite

### v0.3
- Schedule trigger (interval or daily) with a background scheduler and missed-run catch-up
- Webhook trigger with secret URLs, rotation, rate and size limits
- Active/inactive switch per workflow, trigger and next-run shown in the dashboard
- HTTP Request node: GET, POST, PUT, PATCH, DELETE with headers, body and `{{placeholders}}`
- Three more templates, execution history shows what triggered each run
- 126 API tests and a 27-check browser suite

### v0.14
- Production mode: the API serves the built interface (one port), with Docker files and Windows start-at-sign-in scripts
- Fifteen more templates (23 in five groups), a first-run tour and CSV export of run history
- 801 API tests and a 58-check browser suite

### v0.13
- Eight data tools: Sort, Limit, Remove Duplicates, Split Out, Summarize, Rename Keys, Date and Time, Crypto and Encoding
- Per-workflow time limit and run-keeping policy, replay of a failed run from the failed node, owner activity log
- 790 API tests and a 54-check browser suite

### v0.12
- "Run once for each item" mode for action nodes, so a node can process a whole list
- App Action node with Slack, Notion, Airtable, Stripe, Twilio, Linear and HubSpot (simulated-server tests only)
- 751 API tests and a 50-check browser suite

### v0.11
- Responsive layout for phones and tablets (slide-out menu, stacked tables, bottom-sheet inspector)
- Security hardening: security headers, request size limits, Secure cookies behind HTTPS, richer health check
- Credential key rotation (`rotate_key.py`), inbox checks off the scheduler thread, interface crash screen
- 721 API tests and a 49-check browser suite

### v0.10
- Expressions in every {{ }}: arithmetic, comparisons, conditionals, indexing and safe text and list helpers, evaluated without running code
- 707 API tests and a 48-check browser suite

### v0.9
- Queue mode with separate worker processes, atomic job claiming and crash recovery
- SQLite WAL mode for concurrent processes, configurable webhook rate limit
- 550 API tests and a 47-check browser suite

### v0.8
- Parallel execution of branches that are ready together
- Repeat Until, Files and Email (IMAP) trigger
- Per-user projects with visibility enforced on every route
- 527 API tests and a 47-check browser suite

### v0.7
- AI Prompt node for any OpenAI-compatible endpoint (Ollama and LM Studio run free locally)
- Public forms on webhooks, sticky notes, workflow tags with filtering
- Run-history retention, workflow backup and restore, full database download
- Route-by-route access-control test, 473 API tests and a 45-check browser suite

### v0.6
- OAuth 2.0 connections (Google, GitHub, custom) with PKCE and automatic refresh
- Google Sheets and Gmail nodes, OAuth option on the HTTP node
- Durable long waits (up to 7 days) with resume, cancel and restart recovery
- 432 API tests and a 42-check browser suite

### v0.5
- Switch, Code (restricted Python), Stop and Fail and Wait nodes
- Per-node retries, continue-on-error and error-handler workflows
- Database Query (SQLite, Postgres), Read RSS, Telegram and GitHub nodes
- Respond to Webhook
- Users with owner, admin, editor and viewer roles, and change password
- Workflow version history with restore
- 325 API tests and a 39-check browser suite

### v0.4
- Credentials vault: Fernet-encrypted at rest, never returned by the API, redacted from run results
- Engine rebuilt: Loop Over Items, Merge (fan-in), Run Workflow (sub-workflows), pinned node data
- Set Fields, Filter List, Parse CSV, Send Email (SMTP), Send Message (Slack, Discord)
- HTTP authentication (Bearer, Basic, API-key header) and cron schedules
- Editor: undo/redo, copy/paste, duplicate, tidy layout, per-node input and output panel
- 245 API tests and a 34-check browser suite

## Phase 2: True automation engine
- Worker queue (Redis or database-backed)
- Cron expressions, time zones per workflow and durable job queue for schedules
- Signed webhook payloads and replay protection
- Pause/resume, job retry rules, idempotency
- Bounded parallel execution, retries with backoff and error-handler workflows
- Node-level error handling and execution versioning

## Phase 3: Integrations and credentials
- API connector SDK and connector manifest schema
- Credential key rotation
- OAuth integrations (Google, Microsoft, Slack, etc.)
- Email and chat notification action nodes
- CSV, JSON, database, Google Sheets actions
- Files, storage providers and document workflows

## Phase 4: Intelligent workflows
- Optional self-hosted AI model integration
- Document classification, extraction and summarization
- Agent-assisted workflow suggestions with human confirmation
- Template marketplace

## Phase 5: Production SaaS
- PostgreSQL migration, Redis queue, scaled workers
- Multi-user accounts (v0.2 has one owner login), organization isolation, RBAC, audit logs
- Security hardening incl. network egress isolation for untrusted connectors
- Rate limiting, usage quotas, monitoring and disaster recovery
- Multi-tenant deployment and subscription plans (optional)

## Architectural direction
The saved workflow definition (`nodes` and `edges`) is portable JSON. Keep connector runtime and execution engine separate so the UI remains stable as the backend scales.
