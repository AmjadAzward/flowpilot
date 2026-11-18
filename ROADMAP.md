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
- 42 API tests and a 15-check browser end-to-end suite

## Phase 2: True automation engine
- Worker queue (Redis or database-backed)
- Durable schedule/cron triggers
- Secure signed webhook triggers and replay protection
- Pause/resume, job retry rules, idempotency
- Merge nodes (fan-in), subflows, loops and bounded parallel execution
- Node-level error handling and execution versioning

## Phase 3: Integrations and credentials
- API connector SDK and connector manifest schema
- Credential store with encryption at rest and key rotation
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
- Multi-user accounts, organization isolation, RBAC, audit logs
- Security hardening incl. network egress isolation for untrusted connectors
- Rate limiting, usage quotas, monitoring and disaster recovery
- Multi-tenant deployment and subscription plans (optional)

## Architectural direction
The saved workflow definition (`nodes` and `edges`) is portable JSON. Keep connector runtime and execution engine separate so the UI remains stable as the backend scales.
