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

### v0.5 (in progress)
- Switch, Code (restricted Python) and Stop and Fail nodes
- Per-node retries, continue-on-error and error-handler workflows

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
